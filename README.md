# Algodeploy

Algodeploy is a tool for deploying an algorand mainnet node for development purposes without any other dependencies. 
# Installation

To install algodeploy follow the steps below:

1. Clone this repository: `git clone https://github.com/joe-p/algodeploy`
2. Install requirements: `poetry install`

# Usage
**Note:** On Windows, prefix all commands with `py` to prevent a new Window from being opened

## Create Node
To create and start a mainnet node: `./algodeploy.py create`

## Update Node
To udpate the node to the latest version: `./algodeploy.py update`

## Stop Node
To stop `algod` and `kmd`: `./algodeploy.py stop`

## Start Node
To start `algod` and `kmd`: `./algodeploy.py start`

## Goal Commands
To run `goal` commands: `./algodeploy.py goal ...`

## Catchup Node
To catch the node up to mainnet: `./algodeploy.py catchup`


# Supported Environments
On most systems, algodeploy will download the latest official binaries. On Windows, however, the binaries must be built from source and are not officially supported. The functionality on all systems is the same.

| Operating System | Architecture | Install Method | Officially Supported |
| --- | --- | --- | --- |
| Linux | x86_64 | Official binaries | Yes |
| Mac | x86_64 | Official binaries | Yes |
| Mac | arm64 | Official binaries | Yes |
| Windows | x86_64 | Built from source | No |
