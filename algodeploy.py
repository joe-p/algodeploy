#!/usr/bin/env python3
"""algodeploy

Usage:
  algodeploy create [--base-dir=PATH] [--force-download] [<release>]
  algodeploy update [--force-download] [<release>]
  algodeploy catchup
  algodeploy start
  algodeploy stop
  algodeploy status
  algodeploy goal [<goal_args>...]

Options:
  -h --help     Show this screen.
  --version     Show version.
"""
from docopt import docopt
import urllib.request
from tqdm import tqdm
from pathlib import Path
import platform
import requests
import re
import tarfile
import shutil
import subprocess
import sys
import json
from yaspin import yaspin
import time

DEFAULT_ALGOD_PORT = 8888
DEFAULT_KMD_PORT = 9999


# https://stackoverflow.com/a/53877507
class DownloadProgressBar(tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


class AlgoDeploy:
    def __init__(self):
        self.home_dir = Path.home()
        self.algodeploy_dir = Path.joinpath(self.home_dir, ".algodeploy")
        self.tmp_dir = Path.joinpath(self.algodeploy_dir, "tmp")
        self.download_dir = Path.joinpath(self.algodeploy_dir, "downloads")
        self.msys_dir = Path.joinpath(self.home_dir, "msys64")

        self.config_file = Path.joinpath(self.algodeploy_dir, "config.json")

        if (self.config_file).exists():
            config = json.loads(self.config_file.read_text())
            if config["base_dir"]:
                self.base_dir = Path(config["base_dir"])

        if not hasattr(self, "base_dir"):
            self.base_dir = Path.joinpath(self.algodeploy_dir)

        self.bin_dir = Path.joinpath(self.base_dir, "bin")
        self.data_dir = Path.joinpath(self.base_dir, "data")

    def config(self):
        self.start()
        kmd_dir = list(self.data_dir.glob("kmd-*"))[0]
        kmd_config = Path.joinpath(kmd_dir, "kmd_config.json")
        self.update_json(
            kmd_config, address=f"0.0.0.0:{DEFAULT_KMD_PORT}", allowed_origins=["*"]
        )

        algod_config = Path.joinpath(self.data_dir, "config.json")
        self.update_json(
            algod_config,
            EndpointAddress=f"0.0.0.0:{DEFAULT_ALGOD_PORT}",
            EnableDeveloperAPI=True,
        )

        self.stop()

        self.goal("node generatetoken")
        shutil.move(
            Path.joinpath(self.data_dir, "algod.token"),
            Path.joinpath(kmd_dir, "kmd.token"),
        )
        self.goal("node generatetoken")

    def stop(self, silent=True):
        self.goal("node stop", exit_on_error=False, silent=silent)
        self.goal("kmd stop", exit_on_error=False, silent=silent)

    def start(self, silent=True):
        self.goal("node start", silent=silent)
        self.goal("kmd start -t 0", silent=silent)

    def parse_args(self, args=sys.argv[1:]):
        # Handle goal seperately to avoid conflicts with docopt on --help and --version
        if args[0] == "goal":
            self.goal(" ".join(args[1:]), silent=False)
            return

        arguments = docopt(__doc__, args, version="algodeploy 0.1.0")
        if arguments["create"]:
            self.create(
                release=arguments["<release>"] or "stable",
                force_download=arguments["--force-download"],
                base_dir=arguments["--base-dir"],
            )
        elif arguments["start"]:
            self.start(silent=False)
        elif arguments["stop"]:
            self.stop(silent=False)
        elif arguments["status"]:
            self.goal("node status", silent=False)
        elif arguments["catchup"]:
            self.catchup()
        elif arguments["update"]:
            self.update(
                release=arguments["<release>"] or "stable",
                force_download=arguments["--force-download"],
            )

    def update_json(self, file, **kwargs):
        if Path.exists(file):
            with open(file, "r+") as f:
                data = json.load(f)
                data = {**data, **kwargs}
                f.seek(0)
                json.dump(data, f, indent=4)
                f.truncate()
        else:
            with open(file, "w+") as f:
                json.dump(kwargs, f, indent=4)
                f.truncate()

    def goal(self, args, exit_on_error=True, silent=True):
        goal_path = Path.joinpath(self.bin_dir, "goal")

        if platform.system() == "Windows":
            goal_path = Path.joinpath(self.bin_dir, "goal.exe")

        self.cmd(f"{goal_path} -d {self.data_dir} {args}", exit_on_error, silent=silent)

    def extract_archive(self, tarball, dir):
        with yaspin(text=f"Extracting {tarball} to {dir}"):
            with tarfile.open(tarball) as f:
                f.extractall(dir)

    def create_tarball(self, tarball, dir_dict: dict[str, Path]):
        with yaspin(text=f"Creating {tarball}"):
            with tarfile.open(
                tarball,
                "w:gz",
            ) as tar:
                for name in dir_dict:
                    tar.add(dir_dict[name], arcname=name)

    def catchup(self):
        catchpoint = requests.get(
            "https://algorand-catchpoints.s3.us-east-2.amazonaws.com/channel/mainnet/latest.catchpoint"
        ).text
        self.goal(f"node catchup {catchpoint}")

    def update(self, release, force_download=False):
        version_string = self.get_version(release)  # For example: v3.10.0-stable
        version = re.findall("\d+\.\d+\.\d+", version_string)[0]  # For example: 3.10.0
        release_channel = re.findall("-(.*)", version_string)[0]  # For example: stable
        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine == "x86_64":
            machine = "amd64"

        algodeploy_tarball = Path.joinpath(
            Path.joinpath(self.algodeploy_dir, "archives"),
            f"algodeploy_{system}-{machine}_{version_string}.tar.gz",
        )

        self.stop()

        if not force_download and algodeploy_tarball.exists():
            self.extract_archive(algodeploy_tarball, self.tmp_dir)
            shutil.rmtree(self.bin_dir)
            shutil.move(Path.joinpath(self.tmp_dir, "bin"), self.base_dir)
            for exe in self.bin_dir.glob("*"):
                if exe.name not in ["algod", "goal", "kmd"]:
                    exe_path = Path.joinpath(self.bin_dir, exe)
                    exe_path.unlink()
        else:
            tarball = f"node_{release_channel}_{system}-{machine}_{version}.tar.gz"
            tarball_path = Path.joinpath(self.download_dir, tarball)
            aws_url = f"https://algorand-releases.s3.amazonaws.com/channel/{release_channel}/{tarball}"

            try:
                self.download_url(aws_url, tarball_path)

            except Exception as e:
                print(f"Error downloading {aws_url}: {e}")
                self.build_from_source(version_string, move_data_files=False)

        self.start()

    def create(self, release, force_download, base_dir=None):
        # Stop algod and kmd if they are running to prevent orphaned processes
        self.stop()

        if base_dir:
            self.base_dir = Path(base_dir).resolve()
            self.update_json(self.config_file, base_dir=self.base_dir.__str__())
            self.data_dir = Path.joinpath(self.base_dir, "data")
            self.bin_dir = Path.joinpath(self.base_dir, "bin")

        version_string = self.get_version(release)  # For example: v3.10.0-stable
        version = re.findall("\d+\.\d+\.\d+", version_string)[0]  # For example: 3.10.0
        release_channel = re.findall("-(.*)", version_string)[0]  # For example: stable
        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine == "x86_64":
            machine = "amd64"

        archive_dir = Path.joinpath(self.algodeploy_dir, "archives")

        algodeploy_tarball = Path.joinpath(
            Path.joinpath(self.algodeploy_dir, "archives"),
            f"algodeploy_{system}-{machine}_{version_string}.tar.gz",
        )

        # remove previous directory for a clean install
        shutil.rmtree(path=self.data_dir, ignore_errors=True)
        shutil.rmtree(path=self.bin_dir, ignore_errors=True)

        self.download_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.bin_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.data_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        archive_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        if not force_download and algodeploy_tarball.exists():
            self.extract_archive(algodeploy_tarball, self.base_dir)
        else:
            tarball = f"node_{release_channel}_{system}-{machine}_{version}.tar.gz"
            tarball_path = Path.joinpath(self.download_dir, tarball)
            aws_url = f"hxttps://algorand-releases.s3.amazonaws.com/channel/{release_channel}/{tarball}"

            try:
                self.download_url(aws_url, tarball_path)

            except Exception as e:
                print(f"Error downloading {aws_url}: {e}")
                self.build_from_source(version_string)
                download_error = e

            if not download_error:
                with yaspin(text="Extracting node software"):
                    with tarfile.open(tarball_path) as f:
                        f.extractall(path=self.tmp_dir)

                    tmp_data = Path.joinpath(self.tmp_dir, "data")
                    shutil.move(
                        Path.joinpath(tmp_data, "config.json.example"),
                        Path.joinpath(tmp_data, "config.json"),
                    )
                    shutil.move(
                        Path.joinpath(self.tmp_dir, "genesis", "genesis.json"),
                        Path.joinpath(tmp_data, "genesis.json"),
                    )

                    tmp_bin = Path.joinpath(self.tmp_dir, "bin")

                    for exe in tmp_bin.glob("*"):
                        if exe.name not in ["algod", "goal", "kmd"]:
                            exe_path = Path.joinpath(tmp_bin, exe)
                            exe_path.unlink()

                    shutil.move(tmp_bin, self.bin_dir)

            with yaspin(text="Performing initial node configuration"):
                self.config()

            self.create_tarball(
                algodeploy_tarball, {"bin": self.bin_dir, "data": self.data_dir}
            )

        with yaspin(text="Waiting for node to start"):
            self.start()

            token = Path.joinpath(self.data_dir, "algod.token").read_text()
            previous_last_round = 0

            # Sometimes when starting a node for the first time it freezes for a couple of seconds before you can actually start a catchup
            # This loop will wait until the node actually starts syncing before running "goal node catchup"
            while True:
                response = requests.get(
                    f"http://localhost:{DEFAULT_ALGOD_PORT}/v2/status",
                    headers={"X-Algo-API-Token": token},
                )
                if response.status_code != 200:
                    raise ConnectionError(
                        f"HTTP {response.status_code}: {response.text}"
                    )

                last_round = response.json()["last-round"]

                # somtimes the node stays stuck at a low initial round for a while, so we only break out of the loop if the round has increased
                if previous_last_round != 0 and last_round > previous_last_round:
                    break

                previous_last_round = last_round
                time.sleep(0.5)

        self.catchup()
        print('Now catching up to network. Use "algodeploy status" to check progress')

    def download_url(self, url, output_path):
        """
        Download a file from a URL to a specified path with a progress bar
        """
        with DownloadProgressBar(
            unit="B", unit_scale=True, miniters=1, desc=url.split("/")[-1], leave=False
        ) as t:
            urllib.request.urlretrieve(
                url, filename=output_path, reporthook=t.update_to
            )

    def get_version(self, match):
        """
        Get the latest release from github that matches match
        """
        with yaspin(text="Getting latest node version"):
            releases = requests.get(
                "https://api.github.com/repos/algorand/go-algorand/releases"
            ).json()
            for release in releases:
                if match in release["tag_name"]:
                    return release["tag_name"]

    # https://stackoverflow.com/a/57970619
    def cmd(self, cmd_str, exit_on_error=True, silent=False):
        """
        Execute a system command with realtime output
        """
        if not silent:
            print(f"+ {cmd_str}")

        process = subprocess.Popen(
            cmd_str,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
            encoding="utf-8",
            errors="replace",
        )

        while True:
            realtime_output = process.stdout.readline()

            if realtime_output == "" and process.poll() is not None:
                break

            if not silent and realtime_output:
                print(realtime_output.strip(), flush=True)

        rc = process.wait()
        if exit_on_error and rc != 0:
            exit(rc)

        return rc

    def msys_cmd(self, cmd_str, exit_on_error=True, silent=False):
        """
        On Windows, execute a command with realtime output in a msys2/MINGW64 shell
        """
        # Change paths in the command to be POSIX style
        cmd_str = cmd_str.replace("\\", "/")
        cmd_str = cmd_str.replace("C:", "/c")

        env_path = Path.joinpath(self.msys_dir, "usr/bin/env.exe")
        self.cmd(
            f'{env_path} MSYSTEM=MINGW64 /usr/bin/bash -lc "{cmd_str}"',
            exit_on_error,
            silent,
        )

    def prompt(self, text):
        reply = None
        while reply not in ("y", "n"):
            reply = input(f"{text} (y/n): ").casefold()
        return reply == "y"

    def build_from_source(self, tag, move_data_files=True):
        cmd_function = self.cmd

        if platform.system() == "Windows":
            # Install msys2 if it's not already installed.
            # Home directory the install path in case user isn't admin
            if not Path.joinpath(self.msys_dir, "usr/bin/env.exe").is_file():
                if not self.prompt(f"Install msys2 to {self.msys_dir}?"):
                    exit()

                installer_path = Path.joinpath(
                    self.download_dir, "msys2-x86_64-20220904.exe"
                )
                self.download_url(
                    "https://github.com/msys2/msys2-installer/releases/download/2022-09-04/msys2-x86_64-20220904.exe",
                    installer_path,
                )
                self.cmd(
                    f"{installer_path} install --root {self.msys_dir} --confirm-command"
                )

            cmd_function = self.msys_cmd
            # pacman can sometimes hang when checking space in msys2 shell, so it has been disabled
            cmd_function("sed -i 's/^CheckSpace/#CheckSpace/g' /etc/pacman.conf")

            # Download and install go package from mirror because pacman can sometimes be slow
            go_pkg = Path.joinpath(
                self.download_dir, "mingw-w64-x86_64-go-1.19-1-any.pkg.tar.zst"
            )
            self.download_url(
                "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-go-1.19-1-any.pkg.tar.zst",
                go_pkg,
            )
            cmd_function(f"pacman -U --noconfirm --needed {go_pkg}")

        # Download archive of given tag from github
        tarball_path = Path.joinpath(self.download_dir, f"{tag}.tar.gz")
        self.download_url(
            f"https://github.com/algorand/go-algorand/archive/{tag}.tar.gz",
            tarball_path,
        )

        tarball = tarfile.open(tarball_path)

        # Get the name of the directory that the archive will extract to and remove it if it exists
        src_dir = Path.joinpath(self.tmp_dir, Path(tarball.getnames()[0]).name)
        shutil.rmtree(path=src_dir, ignore_errors=True)

        tarball.extractall(path=self.tmp_dir)
        tarball.close()

        cmd_function(f"cd {src_dir} && GOPATH=$HOME/go ./scripts/configure_dev.sh")
        cmd_function(f"cd {src_dir} && GOPATH=$HOME/go make")

        if move_data_files:
            shutil.move(
                Path.joinpath(src_dir, "installer", "config.json.example"),
                Path.joinpath(self.data_dir, "config.json"),
            )

            shutil.move(
                Path.joinpath(
                    src_dir, "installer", "genesis", "mainnet", "genesis.json"
                ),
                Path.joinpath(self.data_dir, "genesis.json"),
            )

        shutil.rmtree(self.bin_dir)
        self.bin_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        for bin in ["algod", "goal", "kmd", "tealdbg"]:
            if platform.system() == "Windows":
                bin_path = Path.joinpath(
                    self.msys_dir,
                    "home",
                    self.home_dir.name,
                    "go",
                    "bin",
                    f"{bin}.exe",
                )
            else:
                bin_path = Path.joinpath(self.home_dir, "go", "bin", bin)

            shutil.copyfile(
                bin_path,
                Path.joinpath(self.bin_dir, bin_path.name),
            )


if __name__ == "__main__":
    ad = AlgoDeploy()
    ad.parse_args()
