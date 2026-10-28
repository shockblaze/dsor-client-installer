# DSO Installer

Downloads and installs the DSO client. The client is wired up to a launcher compatible with the DSO Reconstruction projects.

## Usage

Double-click `run.cmd`. It:

1. finds Python 3.12 or newer (64-bit), the interpreter the DSO server runs on, or offers to install Python 3.12 for your user account (no administrator rights);
2. downloads the current client from Bigpoint's CDN and unpacks its bundles with simo8902's [drakensang-nb3-bundle-extractor](https://github.com/simo8902/drakensang-nb3-bundle-extractor) into `dso-client` (about 2 GB to download, 4.7 GB on disk);
3. builds the launcher, points it at the client's `dro_client64.exe` (`dro_client.exe` on 32-bit Windows) and puts a `DSOLauncher` shortcut beside `run.cmd`.

Then start `DSOLauncher` to log in and play.

On a first install, `run.cmd` also offers to download the [DSO Reconstruction server](https://github.com/DSO-Reconstruction/DSOR_Server) into `DSOR_Server` (with Git, as a clone that `git pull` updates). Once the client is installed, the server's `data` folder gets what its `README.txt` files ask for, copied from the client's `export_win32`: `db/static.db4`, each map's `.db4`, `.map` and `.col` in `maps/`, and the navigation meshes (`.tok`) with `navshapes.bin` in `navigation/`, and the whole `language` folder as `data/language` (`data/language/en`, ...). The client's `quests/quests.pbxml` is decoded into `data/quests.xml` with `pbxml_to_xml.py` (an existing `quests.xml` is kept). A server that already has its `data/db/static.db4` is left as it is. Its README explains how to start it.

Every run then applies the server's asset patches to the client, and to the server's data when it has the client's files: the return gates between Greyling Forest, Tidal Wildwood and Kingshill (the server's `tools/patch_k_map_return_gates.py`), and the admin status effects (`tools/install_admin_effects.py`, which keeps the server's and the client's `static.db4` identical so the effects' ids match). Both do nothing once applied and back up each file beside it before changing it. The patches come from the server in `DSOR_Server`, or from another checkout given with `--server`, for example `run.cmd --launcher-only --server D:\DSO\DSOR_Server`. A client reinstalled from a newer build also needs the server's data refreshed from it (`--server-data`) before the admin effects can be installed; the installer says so when that is the case.

When `dso-client` already holds a client, `run.cmd` asks whether to keep it (only the launcher is set up) or to reinstall (the client files are deleted, then the current version is installed). From a console:

```
run.cmd                   install into dso-client
run.cmd D:\Games\DSO      install into another folder
run.cmd --reinstall       delete the installed client, install the current version
run.cmd --launcher-only   keep the installed client, set up the launcher
run.cmd --server PATH     apply the patches of the DSOR_Server checkout at PATH
run.cmd --help            all of the installer's options
```

## A server on Linux

`run-server-linux.sh` sets up the DSO Reconstruction server on Linux with the client's data. It installs no game client and no launcher:

1. it finds a 64-bit Python 3.12 or newer: `$DSO_PYTHON` when it is set (as the server's `start.sh` takes it), otherwise the first of `python3`, `python3.14`, `python3.13`, `python3.12` and `python`. If there is none, it prints the install command for the system's package manager. Debian 13 and Ubuntu 24.04 or newer ship one as `python3`; on older releases, `uv python install 3.12` installs one for your user only;
2. it downloads the server into `DSOR_Server` (with Git, as a clone that `git pull` updates), unless the server is already there;
3. it downloads and unpacks the client's game data into `dso-client` as `run.cmd` does, but only `export_win32` (`dso_installer.py --data-only`: no executables, and the launcher is left alone);
4. it fills the server's `data` folder as described above when the server has no `data/db/static.db4` yet, or after a reinstall, then applies the server's asset patches.

Then start the server with `bash DSOR_Server/start.sh`. The script prints that line with `DSO_PYTHON=...` in front when the Python it found is not `python3`.

```
bash run-server-linux.sh                 set up the server
bash run-server-linux.sh --reinstall     download the current data again, refresh the server's from it
bash run-server-linux.sh --no-download   keep the downloaded data, apply the server's patches
bash run-server-linux.sh --help          all of the installer's options
```

## What is in here

| Path | What |
|---|---|
| `run.cmd` | the entry point above |
| `run-server-linux.sh` | sets up the DSO server on Linux with the client's data, without the client |
| `dso_installer.py` | the download and extraction (Python, standard library only) |
| `pbxml_to_xml.py` | decodes the client's `.pbxml` packs into XML, here the quests for the server |
| `launcher/` | the login launcher, from the DSO server's `dso_custom_client` ([its README](launcher/README.md)) |
| `dso-client/` | the installed client, written by `run.cmd` (only its `export_win32` data with `run-server-linux.sh`) |
| `DSOR_Server/` | the DSO Reconstruction server, when `run.cmd` was asked to download it or `run-server-linux.sh` set it up |
| `DSOLauncher.lnk`, `launcher-client.json` | the launcher's shortcut and its settings (with the current session), written by `run.cmd` |

## Licensing

### Copyright, trademarks, and third-party material

This is an independent, unofficial research project. It is not affiliated with, endorsed by, or sponsored by Bigpoint GmbH or the owners of Drakensang Online. “Drakensang Online,” related names, logos, characters, artwork, music, client software, and game content remain the property of their respective rights holders. This repository does not claim ownership of or grant rights to that material.

Client files listed above are not distributed here. Do not commit or redistribute game client files, database dumps, maps, language files, account data, captures, or other third-party material unless you have the necessary rights and permission. Keep local-only assets out of Git. Repository privacy does not grant rights to third-party material.
