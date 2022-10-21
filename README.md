# Algodeploy

Algodeploy is a tool for deploying an algorand node for development purposes without any other dependencies. A local network will be created with a non-archival instance of `algod` that has developer APIs enabled. The connection information (address, port, and token) is the same as [sandbox](https://github.com/algorand/sandbox).

# Installation

To install algodeploy follow the steps below:

1. Clone this repository: `git clone https://github.com/joe-p/algodeploy`
2. Install requirements 
    * Mac/Linux: `cd algodeploy && pip install -r requirements.txt`
    * Windows: `cd algodeploy && py -m pip install -r requirements.txt`)

# Usage
**Note:** On Windows, prefix all commands with `py` to prevent a new Window from being opened

## Create Local Network
To create and start a localnetwork: `./algodeploy.py create`

## Stop Local Network
To stop `algod` and `kmd`: `./algodeploy.py stop`

## Start Local Network
To start `algod` and `kmd`: `./algodeploy.py start`

## Goal Commands
To run `goal` commands: `./algodeploy.py goal ...`

# Supported Environments
On most systems, algodeploy will download the latest official binaries. On Windows, however, the binaries must be built from source and are not officially supported. The functionality on all systems is the same.

| Operating System | Architecture | Install Method | Officially Supported |
| --- | --- | --- | --- |
| Linux | x86_64 | Official binaries | Yes |
| Mac | x86_64 | Official binaries | Yes |
| Mac | arm64 | Official binaries | Yes |
| Windows | x86_64 | Built from source | No |
