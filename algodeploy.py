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

        kmd_token = open(Path.joinpath(kmd_dir, "kmd.token"), 'w')
        algod_token = open(Path.joinpath(self.data_dir, "algod.token"), 'w')
        kmd_token.write("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        algod_token.write("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def parse_args(self, args=sys.argv[1:]):
        arguments = docopt(__doc__, args, version='algodeploy 0.1.0')
        if arguments['create']:
            self.create()
        elif arguments['start']:
            self.goal("node start")
            self.goal('kmd start -t 0')
        elif arguments['stop']:
            self.goal("node stop")
            self.goal("kmd stop")
        elif arguments['status']:
            self.goal("node status")
        elif arguments['goal']:
            self.goal(' '.join(args[1:]))

    def update_json(self, file, **kwargs):
        if Path.exists(file):
            with open(file, 'r+') as f:
                data = json.load(f)
                data = { **data, **kwargs }
                f.seek(0)        
                json.dump(data, f, indent=4)
                f.truncate()
        else:
            with open(file, 'w+') as f:
                json.dump(kwargs, f, indent=4)
                f.truncate()

    def goal(self, args):
        self.cmd(f'{Path.joinpath(self.bin_dir, "goal")} -d {self.data_dir} {args}')

    def create(self):
        self.download_dir.mkdir(mode = 0o755, parents=True, exist_ok=True)

        version_string = self.get_version()
        version = re.findall('\d+\.\d+\.\d+', version_string)[0]
        system = platform.system().lower()
        machine = platform.machine().lower()

        tarball = f'node_stable_{system}-{machine}_{version}.tar.gz'
        tarball_path = Path.joinpath(self.download_dir, tarball)
        url = f'https://github.com/algorand/go-algorand/releases/download/{version_string}/{tarball}'

        shutil.rmtree(path=self.localnet_dir, ignore_errors=True)
        try:
            self.download_url(url, tarball_path)
            print("Extracting node software...")
            file = tarfile.open(tarball_path)
            file.extractall(path=self.localnet_dir)
            file.close()
            shutil.rmtree(Path.joinpath(self.localnet_dir, "data"))
            shutil.rmtree(Path.joinpath(self.localnet_dir, "genesis"))
            shutil.rmtree(Path.joinpath(self.localnet_dir, "test-utils"))

        except:
            print("Failed to download node software. Building from source...")
            self.build_from_source(version_string)

        template_path = Path.joinpath(self.download_dir, "template.json")

        print("Downloading localnet template...")
        self.download_url("https://raw.githubusercontent.com/joe-p/docker-algorand/master/algorand-node/template.json", template_path)
        
        print("Creating localnet...")
        self.goal(f'network create --network localnet --template {template_path} --rootdir {Path.joinpath(self.localnet_dir, "data")}')
        
        print("Configuring localnet...")
        self.goal('node start')
        self.goal('kmd start -t 0')
        self.config()
        self.goal('node stop')
        self.goal('kmd stop')

        print("Starting localnet...")
        self.goal('node start')
        self.goal('kmd start -t 0')
        self.goal('node status')

    def download_url(self, url, output_path):
        with DownloadProgressBar(unit='B', unit_scale=True,
                                miniters=1, desc=url.split('/')[-1]) as t:
            urllib.request.urlretrieve(url, filename=output_path, reporthook=t.update_to)

    def get_version(self):
        print("Getting latest node version...")
        releases = requests.get("https://api.github.com/repos/algorand/go-algorand/releases").json()
        for release in releases:
            if "stable" in release["tag_name"]:
                return release["tag_name"]

    # https://stackoverflow.com/a/57970619
    def cmd(self, cmd_str):
        process = subprocess.Popen(
            cmd_str,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
            encoding='utf-8',
            errors='replace'
        )

        while True:
            realtime_output = process.stdout.readline()

            if realtime_output == '' and process.poll() is not None:
                break

            if realtime_output:
                print(realtime_output.strip(), flush=True)
        
        rc = process.wait()
        if rc != 0:
            exit(rc)

    def msys_cmd(self, cmd_str):
        cmd_str = cmd_str.replace("\\", "/")
        cmd_str = cmd_str.replace("C:", "/c")

        self.cmd(f'C:\\msys64\\usr\\bin\\env MSYSTEM=MINGW64 /usr/bin/bash -lc "{cmd_str}"')

    def prompt(self, text):
        reply = None
        while reply not in ("y", "n"):
            reply = input(f"{text} (y/n): ").casefold()
        return (reply == "y")
    
    def build_from_source(self, tag):
        cmd_function = self.cmd

        if platform.system() == "Windows":
            if not Path('C:\\msys64\\usr\\bin\\env').is_file():
                if not self.prompt('Install msys2 to C:\\msys64?'):
                    exit()

                installer_path = Path.joinpath(self.download_dir, 'msys2-x86_64-20220904.exe')
                self.download_url('https://github.com/msys2/msys2-installer/releases/download/2022-09-04/msys2-x86_64-20220904.exe', installer_path)
                self.cmd(f'{installer_path} install --root C:\msys64 --confirm-command')

            cmd_function = self.msys_cmd
            cmd_function("sed -i 's/^CheckSpace/#CheckSpace/g' /etc/pacman.conf")
            cmd_function("pacman -S --disable-download-timeout --noconfirm --needed mingw-w64-x86_64-go")

        tarball_path = Path.joinpath(self.download_dir, f'{tag}.tar.gz')
        self.download_url(f'https://github.com/algorand/go-algorand/archive/{tag}.tar.gz', tarball_path)
        
        tarball = tarfile.open(tarball_path)
        src_dir = Path.joinpath(self.download_dir, Path(tarball.getnames()[0]).name)
        shutil.rmtree(path=src_dir, ignore_errors=True)

        tarball.extractall(path=self.download_dir)
        tarball.close()
        
        cmd_function(f"cd {src_dir} && GOPATH=$HOME/go ./scripts/configure_dev.sh")
        cmd_function(f"cd {src_dir} && GOPATH=$HOME/go make")
        cmd_function(f"cd {src_dir} && mkdir -p ../../localnet/bin && cp $HOME/go/bin/* ../../localnet/bin/")

if __name__ == "__main__":
    ad = AlgoDeploy()
    ad.parse_args()