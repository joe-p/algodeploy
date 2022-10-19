#!/usr/bin/env python3
"""algodeploy

Usage:
  algodeploy create
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
        self.download_dir = Path.joinpath(self.algodeploy_dir, "downloads")
        self.localnet_dir = Path.joinpath(self.algodeploy_dir, "localnet")
        self.data_dir = Path.joinpath(self.localnet_dir, "data", "Node")
        self.bin_dir = Path.joinpath(self.localnet_dir, "bin")
        self.msys_dir = Path.joinpath(self.home_dir, "msys64")

    def config(self):
        kmd_dir = list(self.data_dir.glob("kmd-*"))[0]
        kmd_config = Path.joinpath(kmd_dir, "kmd_config.json")
        self.update_json(kmd_config, address="0.0.0.0:4002", allowed_origins=["*"])

        algod_config = Path.joinpath(self.data_dir, "config.json")
        self.update_json(
            algod_config,
            EndpointAddress="0.0.0.0:4001",
            EnableDeveloperAPI=True,
            Archival=False,
            IsIndexerActive=False,
        )

        token = "a" * 64
        with open(Path.joinpath(kmd_dir, "kmd.token"), "w") as f:
            f.write(token)
        with open(Path.joinpath(self.data_dir, "algod.token"), "w") as f:
            f.write(token)

    def parse_args(self, args=sys.argv[1:]):
        arguments = docopt(__doc__, args, version="algodeploy 0.1.0")
        if arguments["create"]:
            # Stop algod and kmd if they are running to orphaned processes
            self.goal("node stop", exit_on_error=False, silent=True)
            self.goal("kmd stop", exit_on_error=False, silent=True)
            self.create()
        elif arguments["start"]:
            self.goal("node start")
            self.goal("kmd start -t 0")
        elif arguments["stop"]:
            self.goal("node stop")
            self.goal("kmd stop")
        elif arguments["status"]:
            self.goal("node status")
        elif arguments["goal"]:
            self.goal(" ".join(args[1:]))

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

    def goal(self, args, exit_on_error=True, silent=False):
        self.cmd(
            f'{Path.joinpath(self.bin_dir, "goal")} -d {self.data_dir} {args}',
            exit_on_error,
            silent,
        )

    def create(self):
        self.download_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        version_string = self.get_version()  # For example: v3.10.0-stable
        version = re.findall("\d+\.\d+\.\d+", version_string)[0]  # For example: 3.10.0
        system = platform.system().lower()
        machine = platform.machine().lower()

        tarball = f"node_stable_{system}-{machine}_{version}.tar.gz"
        tarball_path = Path.joinpath(self.download_dir, tarball)
        url = f"https://github.com/algorand/go-algorand/releases/download/{version_string}/{tarball}"

        # remove previous localnet directory for a clean install
        shutil.rmtree(path=self.localnet_dir, ignore_errors=True)

        # First attempt to download tarball, but fall back to building from source
        try:
            self.download_url(url, tarball_path)
            print("Extracting node software...")
            with tarfile.open(tarball_path) as f:
                f.extractall(path=self.localnet_dir)
            shutil.rmtree(Path.joinpath(self.localnet_dir, "data"))
            shutil.rmtree(Path.joinpath(self.localnet_dir, "genesis"))
            shutil.rmtree(Path.joinpath(self.localnet_dir, "test-utils"))

        except:
            print("Failed to download node software. Building from source...")
            self.build_from_source(version_string)

        print("Downloading localnet template...")
        template_path = Path.joinpath(self.download_dir, "template.json")
        self.download_url(
            "https://raw.githubusercontent.com/joe-p/docker-algorand/master/algorand-node/template.json",
            template_path,
        )

        print("Creating localnet...")
        self.goal(
            f'network create --network localnet --template {template_path} --rootdir {Path.joinpath(self.localnet_dir, "data")}'
        )

        print("Configuring localnet...")
        # Temporarily start algod and kmd to generate directories and config files
        self.goal("node start", silent=True)
        self.goal("kmd start -t 0", silent=True)
        self.goal("node stop", silent=True)
        self.goal("kmd stop", silent=True)

        self.config()

        print("Starting localnet...")
        self.goal("node start")
        self.goal("kmd start -t 0")
        self.goal("node status")

    def download_url(self, url, output_path):
        """
        Download a file from a URL to a specified path with a progress bar
        """
        with DownloadProgressBar(
            unit="B", unit_scale=True, miniters=1, desc=url.split("/")[-1]
        ) as t:
            urllib.request.urlretrieve(
                url, filename=output_path, reporthook=t.update_to
            )

    def get_version(self):
        """
        Get the latest stable release from github
        """
        print("Getting latest node version...")
        releases = requests.get(
            "https://api.github.com/repos/algorand/go-algorand/releases"
        ).json()
        for release in releases:
            if "stable" in release["tag_name"]:
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

            if realtime_output:
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

    def build_from_source(self, tag):
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
        src_dir = Path.joinpath(self.download_dir, Path(tarball.getnames()[0]).name)
        shutil.rmtree(path=src_dir, ignore_errors=True)

        tarball.extractall(path=self.download_dir)
        tarball.close()

        cmd_function(f"cd {src_dir} && GOPATH=$HOME/go ./scripts/configure_dev.sh")
        cmd_function(f"cd {src_dir} && GOPATH=$HOME/go make")
        cmd_function(
            f"cd {src_dir} && mkdir -p ../../localnet/bin && cp $HOME/go/bin/* ../../localnet/bin/"
        )


if __name__ == "__main__":
    ad = AlgoDeploy()
    ad.parse_args()
