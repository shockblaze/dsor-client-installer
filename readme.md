# DSO Installer

Downloads and installs the DSO client. The client is wired up to a launcher compatible with the DSO Reconstruction projects.

## Usage

Double-click `run.cmd`. It:

1. finds Python 3.12 or newer (64-bit), the interpreter the DSO server runs on, or offers to install Python 3.12 for your user account (no administrator rights);
2. downloads the current client from Bigpoint's CDN and unpacks its bundles with simo8902's [drakensang-nb3-bundle-extractor](https://github.com/simo8902/drakensang-nb3-bundle-extractor) into `dso-client` (about 2 GB to download, 4.7 GB on disk);
3. builds the launcher, points it at the client's `dro_client64.exe` (`dro_client.exe` on 32-bit Windows) and puts a `DSOLauncher` shortcut beside `run.cmd`.

Then start `DSOLauncher` to log in and play.

On a first install, `run.cmd` also offers to download the [DSO Reconstruction server](https://github.com/DSO-Reconstruction/DSOR_Server) into `DSOR_Server` (with Git, as a clone that `git pull` updates). Once the client is installed, the server's `data` folder gets what its `README.txt` files ask for, copied from the client's `export_win32`: `db/static.db4`, each map's `.db4`, `.map` and `.col` in `maps/`, and the navigation meshes (`.tok`) with `navshapes.bin` in `navigation/`, and the whole `language` folder as `data/language` (`data/language/en`, ...). The client's `quests/quests.pbxml` is decoded into `data/quests.xml` with `pbxml_to_xml.py` (an existing `quests.xml` is kept). A server that already has its `data/db/static.db4` is left as it is. Its README explains how to start it.

When `dso-client` already holds a client, `run.cmd` asks whether to keep it (only the launcher is set up) or to reinstall (the client files are deleted, then the current version is installed). From a console:

```
run.cmd                   install into dso-client
run.cmd D:\Games\DSO      install into another folder
run.cmd --reinstall       delete the installed client, install the current version
run.cmd --launcher-only   keep the installed client, set up the launcher
run.cmd --help            all of the installer's options
```

## What is in here

| Path | What |
|---|---|
| `run.cmd` | the entry point above |
| `dso_installer.py` | the download and extraction (Python, standard library only) |
| `pbxml_to_xml.py` | decodes the client's `.pbxml` packs into XML, here the quests for the server |
| `launcher/` | the login launcher, from the DSO server's `dso_custom_client` ([its README](launcher/README.md)) |
| `dso-client/` | the installed client, written by `run.cmd` |
| `DSOR_Server/` | the DSO Reconstruction server, when `run.cmd` was asked to download it |
| `DSOLauncher.lnk`, `launcher-client.json` | the launcher's shortcut and its settings (with the current session), written by `run.cmd` |

## Licensing

### Copyright, trademarks, and third-party material

This is an independent, unofficial research project. It is not affiliated with, endorsed by, or sponsored by Bigpoint GmbH or the owners of Drakensang Online. “Drakensang Online,” related names, logos, characters, artwork, music, client software, and game content remain the property of their respective rights holders. This repository does not claim ownership of or grant rights to that material.

Client files listed above are not distributed here. Do not commit or redistribute game client files, database dumps, maps, language files, account data, captures, or other third-party material unless you have the necessary rights and permission. Keep local-only assets out of Git. Repository privacy does not grant rights to third-party material.
