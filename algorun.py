#!/usr/bin/env python3
"""algorun

Usage:
  algorun create [--base-dir=PATH] [--force-download] [<release>]
  algorun update [--force-download] [<release>]
  algorun catchup
  algorun start
  algorun stop
  algorun status
  algorun goal [<goal_args>...]
  algorun dashboard

Options:
  -h --help     Show this screen.
  --version     Show version.
"""
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
import psutil

import requests
from docopt import docopt
from tqdm import tqdm
from yaspin import yaspin


# https://stackoverflow.com/a/53877507
class DownloadProgressBar(tqdm):
    def update_to(self, b: int = 1, bsize: int = 1, tsize: int = 0) -> None:
        if tsize:
            self.total = tsize
        self.update(b * bsize - self.n)


class algorun:
    def __init__(self) -> None:
        self.home_dir = Path.home()
        self.algorun_dir = Path.joinpath(self.home_dir, ".algorun")
        self.tmp_dir = Path.joinpath(self.algorun_dir, "tmp")
        self.download_dir = Path.joinpath(self.algorun_dir, "downloads")
        self.msys_dir = Path.joinpath(self.home_dir, "msys64")

        self.config_file = Path.joinpath(self.algorun_dir, "config.json")

        if (self.config_file).exists():
            config = json.loads(self.config_file.read_text())
            if "base_dir" in config.keys():
                self.base_dir = Path(config["base_dir"])

        if not hasattr(self, "base_dir"):
            self.base_dir = Path.joinpath(self.algorun_dir)

        self.bin_dir = Path.joinpath(self.base_dir, "bin")
        self.data_dir = Path.joinpath(self.base_dir, "data")

    def config(self) -> None:
        self.start()
        kmd_dir = next(iter(self.data_dir.glob("kmd-*")))
        kmd_config = Path.joinpath(kmd_dir, "kmd_config.json")
        self.update_json(kmd_config, address="0.0.0.0:0", allowed_origins=["*"])

        algod_config = Path.joinpath(self.data_dir, "config.json")
        self.update_json(algod_config, EndpointAddress="0.0.0.0:0")

        self.stop()

        self.goal("node generatetoken")
        shutil.move(
            Path.joinpath(self.data_dir, "algod.token"),
            Path.joinpath(kmd_dir, "kmd.token"),
        )
        self.goal("node generatetoken")

    def stop(self, *, silent: bool = True) -> None:
        self.goal("node stop", exit_on_error=False, silent=silent)
        self.goal("kmd stop", exit_on_error=False, silent=silent)

    def start(self, *, silent: bool = True) -> None:
        self.goal("node start", silent=silent)
        self.goal("kmd start -t 0", silent=silent)

    def parse_args(self, args: list[str] = sys.argv[1:]) -> None:
        # Handle goal seperately to avoid conflicts with docopt on --help and --version
        if args[0] == "goal":
            self.goal(" ".join(args[1:]), silent=False)
            return

        arguments = docopt(__doc__, args, version="algorun 0.1.0")
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
            self.goal("node status -w 1000", silent=False)
        elif arguments["catchup"]:
            self.catchup()
        elif arguments["update"]:
            self.update(
                release=arguments["<release>"] or "stable",
                force_download=arguments["--force-download"],
            )
        elif arguments["dashboard"]:
            self.dashboard()

    def update_json(self, file: Path, **kwargs: dict[str, str]) -> None:
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

    def goal(
        self, args: list[str], *, exit_on_error: bool = True, silent: bool = True
    ) -> None:
        goal_path = Path.joinpath(self.bin_dir, "goal")

        if platform.system() == "Windows":
            goal_path = Path.joinpath(self.bin_dir, "goal.exe")

        self.cmd(
            f"{goal_path} -d {self.data_dir} {args}",
            exit_on_error=exit_on_error,
            silent=silent,
        )

    def extract_archive(self, tarball: Path, target: Path) -> None:
        with yaspin(text=f"Extracting {tarball} to {target}") as y:
            with tarfile.open(tarball) as f:
                f.extractall(target)
            y.text = f"Extracted {tarball} to {target}"
            y.ok("✓")

    def create_tarball(self, tarball: Path, dir_dict: dict[str, Path]) -> None:
        with yaspin(text=f"Creating {tarball}") as y:
            with tarfile.open(
                tarball,
                "w:gz",
            ) as tar:
                for name in dir_dict:
                    tar.add(dir_dict[name], arcname=name)
            y.text = f"Created {tarball}"
            y.ok("✓")

    def catchup(self) -> None:
        catchpoint = requests.get(
            "https://algorand-catchpoints.s3.us-east-2.amazonaws.com/channel/mainnet/latest.catchpoint"
        ).text
        self.goal(f"node catchup {catchpoint}")

    def update(self, release: str, *, force_download: bool = False) -> None:
        version_string = self.get_version(release)  # For example: v3.10.0-stable
        version = re.findall(r"\d+\.\d+\.\d+", version_string)[0]  # For example: 3.10.0
        release_channel = re.findall("-(.*)", version_string)[0]  # For example: stable
        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine == "x86_64":
            machine = "amd64"

        algorun_tarball = Path.joinpath(
            Path.joinpath(self.algorun_dir, "archives"),
            f"algorun_{system}-{machine}_{version_string}.tar.gz",
        )

        self.stop()

        if not force_download and algorun_tarball.exists():
            self.extract_archive(algorun_tarball, self.tmp_dir)
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

    def create(
        self, release: str, base_dir: str | None = None, *, force_download: bool
    ) -> None:
        # Stop algod and kmd if they are running to prevent orphaned processes
        self.stop()

        if base_dir:
            self.base_dir = Path(base_dir).resolve()
            self.update_json(self.config_file, base_dir=self.base_dir.__str__())
            self.data_dir = Path.joinpath(self.base_dir, "data")
            self.bin_dir = Path.joinpath(self.base_dir, "bin")

        version_string = self.get_version(release)  # For example: v3.10.0-stable
        version = re.findall(r"\d+\.\d+\.\d+", version_string)[0]  # For example: 3.10.0
        release_channel = re.findall("-(.*)", version_string)[0]  # For example: stable
        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine == "x86_64":
            machine = "amd64"

        archive_dir = Path.joinpath(self.algorun_dir, "archives")

        algorun_tarball = Path.joinpath(
            Path.joinpath(self.algorun_dir, "archives"),
            f"algorun_{system}-{machine}_{version_string}.tar.gz",
        )

        # remove previous directory for a clean install
        shutil.rmtree(path=self.data_dir, ignore_errors=True)
        shutil.rmtree(path=self.bin_dir, ignore_errors=True)

        self.download_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.bin_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.data_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        archive_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        if not force_download and algorun_tarball.exists():
            self.extract_archive(algorun_tarball, self.base_dir)
        else:
            tarball = f"node_{release_channel}_{system}-{machine}_{version}.tar.gz"
            tarball_path = Path.joinpath(self.download_dir, tarball)
            aws_url = f"https://algorand-releases.s3.amazonaws.com/channel/{release_channel}/{tarball}"

            download_error = None
            try:
                self.download_url(aws_url, tarball_path)

            except Exception as e:
                print(f"Error downloading {aws_url}: {e}")
                self.build_from_source(version_string)
                download_error = e

            if not download_error:
                with yaspin(text=f"Extracting node software to {self.base_dir}") as y:
                    with tarfile.open(tarball_path) as f:
                        f.extractall(path=self.tmp_dir)

                    tmp_data = Path.joinpath(self.tmp_dir, "data")
                    shutil.move(
                        Path.joinpath(tmp_data, "config.json.example"),
                        Path.joinpath(self.data_dir, "config.json"),
                    )
                    shutil.move(
                        Path.joinpath(self.tmp_dir, "genesis", "genesis.json"),
                        Path.joinpath(self.data_dir, "genesis.json"),
                    )

                    tmp_bin = Path.joinpath(self.tmp_dir, "bin")

                    for exe in tmp_bin.glob("*"):
                        if exe.name not in ["algod", "goal", "kmd"]:
                            exe_path = Path.joinpath(tmp_bin, exe)
                            exe_path.unlink()

                    shutil.rmtree(self.bin_dir)
                    shutil.move(tmp_bin, self.base_dir)

                    y.text = f"Extracted node software to {self.base_dir}"
                    y.ok("✓")

            with yaspin(text="Performing initial node configuration"):
                self.config()
                y.text = "Initial node configuration complete"
                y.ok("✓")

            self.create_tarball(
                algorun_tarball, {"bin": self.bin_dir, "data": self.data_dir}
            )

        with yaspin(text="Waiting for node to start") as y:
            self.start()

            token = Path.joinpath(self.data_dir, "algod.token").read_text()
            algod_port = int(Path.joinpath(
                self.data_dir, "algod.net"
            ).read_text().strip().split(":")[-1])

            kmd_dir = next(iter(self.data_dir.glob("kmd-*")))
            kmd_port = int(Path.joinpath(
                kmd_dir, "kmd.net"
            ).read_text().strip().split(":")[-1])

            kmd_config = Path.joinpath(kmd_dir, "kmd_config.json")
            self.update_json(
                kmd_config, address=f"0.0.0.0:{kmd_port}", allowed_origins=["*"]
            )

            algod_config = Path.joinpath(self.data_dir, "config.json")
            self.update_json(
                algod_config,
                EndpointAddress=f"0.0.0.0:{algod_port}",
            )

            previous_last_round = 0

            # Sometimes when starting a node for the first time it freezes for a couple
            # of seconds before you can actually start a catchup
            #
            # This loop will wait until the node actually starts syncing before running
            # "goal node catchup"
            while True:
                response = requests.get(
                    f"http://localhost:{algod_port}/v2/status",
                    headers={"X-Algo-API-Token": token},
                )
                if response.status_code != 200:
                    raise ConnectionError(
                        f"HTTP {response.status_code}: {response.text}"
                    )

                last_round = response.json()["last-round"]

                # somtimes the node stays stuck at a low initial round for a while, so
                # we only break out of the loop if the round has increased
                if previous_last_round != 0 and last_round > previous_last_round:
                    break

                previous_last_round = last_round
                time.sleep(0.5)

            y.text = "Node started"
            y.ok("✓")

        self.catchup()
        print('Now catching up to network. Use "algorun status" to check progress')

    def download_url(self, url: str, output_path: Path) -> None:
        """
        Download a file from a URL to a specified path with a progress bar
        """
        with DownloadProgressBar(
            unit="B", unit_scale=True, miniters=1, desc=url.split("/")[-1], leave=False
        ) as t:
            urllib.request.urlretrieve(
                url, filename=output_path, reporthook=t.update_to
            )

    def get_version(self, match: str) -> None:
        """
        Get the latest release from github that matches match
        """
        with yaspin(text=f"Getting latest node version matching '{match}'") as y:
            releases = requests.get(
                "https://api.github.com/repos/algorand/go-algorand/releases"
            ).json()
            for release in releases:
                if match in release["tag_name"]:
                    y.text = f"Latest node version matching '{match}' is {release['tag_name']}"  # noqa: E501
                    y.ok("✓")
                    return release["tag_name"]

    # https://stackoverflow.com/a/57970619
    def cmd(
        self, cmd_str: str, *, exit_on_error: bool = True, silent: bool = False
    ) -> None:
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

        try:
            while True:
                realtime_output = process.stdout.readline()

                if realtime_output == "" and process.poll() is not None:
                    break

                if not silent and realtime_output:
                    print(realtime_output.strip(), flush=True)
        except KeyboardInterrupt:
            process.terminate()
            exit(0)

        rc = process.wait()
        if exit_on_error and rc != 0:
            exit(rc)

        return rc

    def msys_cmd(
        self, cmd_str: str, *, exit_on_error: bool = True, silent: bool = False
    ) -> None:
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

    def prompt(self, text: str) -> bool:
        reply = None
        while reply not in ("y", "n"):
            reply = input(f"{text} (y/n): ").casefold()
        return reply == "y"

    def build_from_source(self, tag: str, *, move_data_files: bool = True) -> None:
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

            # pacman can sometimes hang when checking space in msys2 shell, so it has
            # been disabled
            cmd_function("sed -i 's/^CheckSpace/#CheckSpace/g' /etc/pacman.conf")

            # Download and install go package from mirror because pacman can sometimes
            # be slow
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

        # Get the name of the directory that the archive will extract to and remove it
        # if it exists
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

        for bin_file in ["algod", "goal", "kmd", "tealdbg"]:
            if platform.system() == "Windows":
                bin_path = Path.joinpath(
                    self.msys_dir,
                    "home",
                    self.home_dir.name,
                    "go",
                    "bin",
                    f"{bin_file}.exe",
                )
            else:
                bin_path = Path.joinpath(self.home_dir, "go", "bin", bin)

            shutil.copyfile(
                bin_path,
                Path.joinpath(self.bin_dir, bin_path.name),
            )

    def dashboard(self) -> None:
        alloctrl_dir = Path.joinpath(self.algorun_dir, "alloctrl-main")
        pid_file = Path.joinpath(alloctrl_dir, "pid")

        if alloctrl_dir.exists():
            if pid_file.exists():
                try:
                    psutil.Process(int(pid_file.read_text())).terminate()
                except psutil.NoSuchProcess:
                    pass

            shutil.rmtree(alloctrl_dir)

        tarball_path = Path.joinpath(self.download_dir, "alloctrl.tar.gz")
        self.download_url(
            "https://github.com/AlgoNode/alloctrl/archive/main.tar.gz",
            tarball_path,
        )

        self.extract_archive(tarball_path, self.algorun_dir)
        algod_port = Path.joinpath(
            self.data_dir, "algod.net"
        ).read_text().strip().split(":")[-1]

        admin_token = Path.joinpath(
            self.data_dir, "algod.admin.token"
        ).read_text().strip()

        with yaspin(text="Setting up allowctrl") as y:
            env = f"""PUBLIC_ALGOD_HOST=127.0.0.1
            PUBLIC_ALGOD_PORT={algod_port}
            SECRET_ALGOD_ADMIN_TOKEN={admin_token}
            PUBLIC_CHECK_VERSION_ON_GITHUB=true
            PUBLIC_ALLOW_EXTERNAL_APIS=true"""

            Path.joinpath(alloctrl_dir, ".env").write_text(env)

            self.cmd(f"cd {alloctrl_dir} && npm install && npm run build", silent=True)
            y.text = "alloctrl setup complete"
            y.ok("✓")

        with yaspin(text="Starting alloctrl") as y:
            process = subprocess.Popen(
                "npm run start",
                cwd=alloctrl_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                encoding="utf-8",
                errors="replace",
            )

            port = 0
            while True:
                realtime_output = process.stdout.readline()

                if realtime_output == "" and process.poll() is not None:
                    break

                if "Listening " in realtime_output:
                    port = realtime_output.split(":")[-1].strip()
                    break

            ps_process = psutil.Process(process.pid)

            pid = ps_process.children()[0].pid

            pid_file.write_text(str(pid))

            y.text = "Dashboard started at http://localhost:" + port
            y.ok("✓")

if __name__ == "__main__":
    ad = algorun()
    ad.parse_args()