"""Freeze the launcher into standalone Windows executables.

Produces two programs in ``dist`` so both front-ends are usable without a
Python install:

    DSOLauncher.exe   the login window, built windowed (no console)
    dso-client.exe    the command line front-end, built with a console

and a shortcut to the login window, ``DSOLauncher.lnk``, in the project root.

Run it with a 64-bit CPython 3.10 or newer (run.cmd does, after installing
the client):

    py -3.12-64 build_exe.py

An interpreter without PyInstaller builds from a virtual environment in
``.venv`` beside this script, made from that interpreter and given PyInstaller
on the first build; the interpreter's own packages are left as they are.

``--onedir`` trades the single-file layout for a folder that starts faster,
and ``--icon`` overrides the icon, which otherwise comes from the configured
game client when that executable can be found.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dso_client import REPO_ROOT, ClientConfig, LauncherError  # noqa: E402

BUILD_ENVIRONMENT = HERE / ".venv"
SHORTCUT_DESCRIPTION = "Log in and start Drakensang Online"


TARGETS = (
    # (entry script, executable name, windowed)
    ("dso_launcher_gui.py", "DSOLauncher", True),
    ("dso_client.py", "dso-client", False),
)


def default_icon() -> Path | None:
    """Borrow the game client's icon so the launcher looks like it belongs."""

    try:
        client = Path(str(ClientConfig().setting("client_path")))
    except LauncherError:
        return None
    return client if client.is_file() else None


def create_shortcut(target: Path) -> Path | None:
    """A shortcut to the login window in the project root, beside run.cmd.

    Windows Script Host writes it, through PowerShell; ``None`` outside a
    project checkout or when that fails.
    """

    if os.name != "nt" or REPO_ROOT is None or not target.is_file():
        return None
    shortcut = REPO_ROOT / f"{target.stem}.lnk"

    def quoted(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        f"$link = (New-Object -ComObject WScript.Shell).CreateShortcut({quoted(shortcut)}); "
        f"$link.TargetPath = {quoted(target)}; "
        f"$link.WorkingDirectory = {quoted(target.parent)}; "
        f"$link.Description = {quoted(SHORTCUT_DESCRIPTION)}; "
        "$link.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"warning: could not create {shortcut}: {exc}", file=sys.stderr)
        return None
    return shortcut


def build_in_environment(argv: list[str]) -> int:
    """Build again from ``.venv``, creating it and installing PyInstaller first."""

    if Path(sys.prefix).resolve() == BUILD_ENVIRONMENT.resolve():
        print(f"error: PyInstaller is missing from {BUILD_ENVIRONMENT}", file=sys.stderr)
        return 2
    scripts = "Scripts" if os.name == "nt" else "bin"
    python = BUILD_ENVIRONMENT / scripts / ("python.exe" if os.name == "nt" else "python")
    try:
        if not python.is_file():
            print(f"Creating the build environment {BUILD_ENVIRONMENT}")
            subprocess.run([sys.executable, "-m", "venv", str(BUILD_ENVIRONMENT)], check=True)
        if subprocess.run([str(python), "-c", "import PyInstaller"], capture_output=True).returncode:
            print("Installing PyInstaller into the build environment")
            subprocess.run(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", "pyinstaller"],
                check=True,
            )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: could not prepare {BUILD_ENVIRONMENT}: {exc}", file=sys.stderr)
        return 2
    return subprocess.run([str(python), str(Path(__file__).resolve()), *argv]).returncode


def build(
    entry: str,
    name: str,
    windowed: bool,
    *,
    onefile: bool,
    icon: Path | None,
    dist: Path,
    work: Path,
) -> None:
    from PyInstaller import __main__ as pyinstaller

    arguments = [
        str(HERE / entry),
        "--name", name,
        "--noconfirm",
        "--clean",
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(work),
        "--paths", str(HERE),
        "--onefile" if onefile else "--onedir",
        "--windowed" if windowed else "--console",
    ]
    if icon is not None:
        arguments += ["--icon", str(icon)]
    # The CLI entry point imports the window lazily for its "gui" command.
    if not windowed:
        arguments += ["--hidden-import", "dso_launcher_gui"]
    arguments += ["--hidden-import", "account_client", "--hidden-import", "dpapi"]
    print(f"\n=== building {name} ===")
    pyinstaller.run(arguments)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_exe.py", description="Build the launcher executables."
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="build a folder per program instead of a single file",
    )
    parser.add_argument("--icon", help="icon source (.ico or .exe)")
    parser.add_argument(
        "--dist", default=str(HERE / "dist"), help="output directory"
    )
    parser.add_argument(
        "--keep-build",
        action="store_true",
        help="keep the intermediate build directory",
    )
    args = parser.parse_args(argv)

    if sys.version_info < (3, 10):
        print(
            f"error: this tool needs Python 3.10 or newer, got "
            f"{sys.version.split()[0]}",
            file=sys.stderr,
        )
        return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return build_in_environment(sys.argv[1:] if argv is None else argv)

    # Anaconda keeps the DLLs its extension modules load (Tcl/Tk, bzip2) in
    # Library\bin; a virtual environment made from it finds them only on the
    # PATH, and without them the window cannot start.
    library = Path(sys.base_prefix) / "Library" / "bin"
    if library.is_dir():
        os.environ["PATH"] = f"{library}{os.pathsep}{os.environ.get('PATH', '')}"

    icon = Path(args.icon) if args.icon else default_icon()
    if icon is not None and not icon.is_file():
        print(f"error: icon source not found: {icon}", file=sys.stderr)
        return 2
    print(f"icon: {icon if icon is not None else '(default PyInstaller icon)'}")

    dist = Path(args.dist)
    work = HERE / "build"
    for entry, name, windowed in TARGETS:
        build(
            entry,
            name,
            windowed,
            onefile=not args.onedir,
            icon=icon,
            dist=dist,
            work=work,
        )
    if not args.keep_build:
        shutil.rmtree(work, ignore_errors=True)

    print("\nBuilt:")
    for _entry, name, _windowed in TARGETS:
        produced = dist / (f"{name}.exe" if not args.onedir else name)
        marker = "ok " if produced.exists() else "MISSING "
        print(f"  {marker}{produced}")
    for _entry, name, windowed in TARGETS:
        if windowed:
            executable = dist / f"{name}.exe" if not args.onedir else dist / name / f"{name}.exe"
            shortcut = create_shortcut(executable)
            if shortcut is not None:
                print(f"  shortcut {shortcut}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
