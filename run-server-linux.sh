#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Set up the DSO Reconstruction server on Linux with the data it reads from
#  the Drakensang Online client: download the server
#  (DSO-Reconstruction/DSOR_Server) into DSOR_Server/ beside this script,
#  then the client's game data from Bigpoint's CDN into dso-client/
#  (dso_installer.py --data-only: export_win32 alone, no executables, no
#  launcher), copy what the server reads into its data folder (--server-data)
#  and apply the server's asset patches.
#
#  Usage:
#    bash run-server-linux.sh                 set up the server
#    bash run-server-linux.sh /srv/dso-data   keep the client's data in another folder (the first argument)
#    bash run-server-linux.sh --reinstall     download the current data again, refresh the server's from it
#    bash run-server-linux.sh --no-download   keep the downloaded data, apply the server's patches
#    bash run-server-linux.sh --help          the installer's options
#
#  Every argument is passed through to dso_installer.py; a relative folder is
#  relative to this script's folder. When the folder already holds the data
#  and neither --reinstall nor --no-download is given, it asks which. A
#  server already in DSOR_Server/ is used as it is (git pull updates a
#  clone). Its data folder gets the client's data when it has no
#  data/db/static.db4 yet, and again after a reinstall.
#
#  Python: the interpreter the server runs on (Python 3.12 or newer, 64-bit).
#  $DSO_PYTHON when it is set, as the server's start.sh takes it; otherwise
#  python3, python3.14, python3.13, python3.12, then python. With none of
#  them, this says how to install one.
# ---------------------------------------------------------------------------

# Started as "sh run-server-linux.sh": the rest is bash.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi

# Always work beside the script, wherever it is started from.
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
cd "$HERE" || exit 1
NAME="$(basename -- "${BASH_SOURCE[0]}")"

SERVER_REPO="DSO-Reconstruction/DSOR_Server"
SERVER_DIR="DSOR_Server"
SERVER_STAGE=".server-download"


# --- Helpers ----------------------------------------------------------------

has() {
    command -v "$1" >/dev/null 2>&1
}

# wants_help ARGS...: whether the installer is only asked for its options.
wants_help() {
    local arg
    for arg in "$@"; do
        case "$arg" in -h|--help) return 0 ;; esac
    done
    return 1
}

# ask PROMPT LETTERS: read one of LETTERS (upper case) into ANSWER; fails
# when there is no terminal to ask on.
ask() {
    ANSWER=""
    [ -t 0 ] || return 1
    local reply
    while read -r -p "$1" reply; do
        reply="${reply^^}"
        if [ "${#reply}" -eq 1 ] && [[ "$2" == *"$reply"* ]]; then
            ANSWER="$reply"
            return 0
        fi
    done
    return 1
}

# download URL FILE
download() {
    if has curl; then
        curl -fL --retry 3 -o "$2" "$1"
    elif has wget; then
        wget -O "$2" "$1"
    else
        echo "  Neither curl nor wget is installed."
        return 1
    fi
}


# --- Data already downloaded ------------------------------------------------

# MODE: empty (install), reinstall, keep or quit. --reinstall and
# --no-download (or --launcher-only) on the command line answer the
# question, and --help needs none.
choose_mode() {
    MODE=""
    wants_help "$@" && return 0
    local arg
    for arg in "$@"; do
        case "$arg" in
            --reinstall) MODE="reinstall" ;;
            --no-download|--launcher-only) MODE="keep" ;;
        esac
    done
    [ -n "$MODE" ] && return 0

    # The folder is the first argument unless that is an option.
    local data_dir="dso-client"
    if [ -n "${1:-}" ] && [ "${1#-}" = "$1" ]; then data_dir="$1"; fi
    [ -d "$data_dir/export_win32" ] || return 0

    echo
    echo "[$NAME] The client's data is already in $data_dir."
    echo
    echo "  K  Keep it: download nothing, only apply the server's patches."
    echo "  R  Reinstall: delete it, download the current version (about 2 GB,"
    echo "     5 to 10 minutes) and refresh the server's data from it."
    echo
    if ! ask "Keep or reinstall [K/R]? " KR; then
        echo "  No answer could be read; nothing was changed. Pass --reinstall or"
        echo "  --no-download to choose without being asked."
        MODE="quit"
        return 0
    fi
    if [ "$ANSWER" = R ]; then MODE="reinstall"; else MODE="keep"; fi
}


# --- The server -------------------------------------------------------------

fetch_server() {
    if [ ! -d "$SERVER_DIR" ]; then
        echo
        echo "[$NAME] Downloading $SERVER_REPO into $SERVER_DIR"
        # With git, a clone that git pull updates; without, the default
        # branch as an archive.
        if has git; then
            GIT_TERMINAL_PROMPT=0 git clone "https://github.com/$SERVER_REPO.git" "$SERVER_DIR"
        else
            download_server_archive
        fi
        local status=$?
        rm -rf "$SERVER_STAGE"
        if [ "$status" -ne 0 ]; then
            echo
            echo "[$NAME] ERROR: the server could not be downloaded. Check the internet"
            echo "  connection and start $NAME again; it is at https://github.com/$SERVER_REPO"
            return 1
        fi
    fi
    if [ ! -d "$SERVER_DIR/data" ]; then
        echo
        echo "[$NAME] ERROR: $SERVER_DIR has no data folder; is it a DSOR_Server checkout?"
        return 1
    fi
}

download_server_archive() {
    rm -rf "$SERVER_STAGE"
    mkdir "$SERVER_STAGE" || return 1
    download "https://github.com/$SERVER_REPO/archive/HEAD.tar.gz" "$SERVER_STAGE/server.tar.gz" || return 1
    tar -xzf "$SERVER_STAGE/server.tar.gz" -C "$SERVER_STAGE" || return 1
    rm "$SERVER_STAGE/server.tar.gz"
    # The archive holds one folder, named after the repository and branch.
    local folder
    for folder in "$SERVER_STAGE"/*/; do
        mv "$folder" "$SERVER_DIR" || return 1
    done
    [ -f "$SERVER_DIR/README.md" ]
}


# --- Interpreter selection --------------------------------------------------

# dso_installer.py runs on 3.9 or newer; the server needs 3.12 or newer and a
# 64-bit build, so one interpreter serves both.
try_python() {
    has "$1" || return 1
    "$1" -c "import lzma, ssl, struct, sys; sys.exit(0 if sys.version_info >= (3, 12) and struct.calcsize('P') == 8 else 1)" </dev/null >/dev/null 2>&1
}

find_python() {
    PY=""
    if [ -n "${DSO_PYTHON:-}" ]; then
        try_python "$DSO_PYTHON" && PY="$DSO_PYTHON"
        return 0
    fi
    local candidate
    for candidate in python3 python3.14 python3.13 python3.12 python; do
        if try_python "$candidate"; then
            PY="$candidate"
            return 0
        fi
    done
}

python_missing() {
    echo
    if [ -n "${DSO_PYTHON:-}" ]; then
        echo "[$NAME] ERROR: DSO_PYTHON ($DSO_PYTHON) is not a 64-bit Python 3.12"
        echo "  or newer with the lzma and ssl modules."
        return
    fi
    echo "[$NAME] ERROR: Python 3.12 or newer (64-bit, with the lzma and ssl"
    echo "  modules) was not found."
    echo
    echo "  The installer and the DSO server both run on it. Install it with your"
    echo "  distribution's packages and start $NAME again:"
    if has apt-get; then echo "    sudo apt install python3"
    elif has dnf; then echo "    sudo dnf install python3"
    elif has pacman; then echo "    sudo pacman -S python"
    elif has zypper; then echo "    sudo zypper install python312"
    fi
    echo "  Debian 12, Ubuntu 22.04 and older releases have no Python 3.12 package;"
    echo "  uv (https://docs.astral.sh/uv/) installs one for your user only:"
    echo "    uv python install 3.12"
    echo "  An interpreter elsewhere is taken from DSO_PYTHON, for example:"
    echo "    DSO_PYTHON=/opt/python3.12/bin/python3 bash $NAME"
}


# --- Main -------------------------------------------------------------------

choose_mode "$@"
[ "$MODE" = quit ] && exit 1

find_python
if [ -z "$PY" ]; then
    python_missing
    exit 1
fi

INSTALL_FLAGS=(--data-only)
if ! wants_help "$@"; then
    fetch_server || exit 1
    # The server's data gets the client's when it has none yet, and again
    # from a reinstalled build; anything else of it is left alone.
    if [ "$MODE" = reinstall ] || [ ! -f "$SERVER_DIR/data/db/static.db4" ]; then
        INSTALL_FLAGS+=(--server-data "$SERVER_DIR")
    fi
fi
[ "$MODE" = reinstall ] && INSTALL_FLAGS+=(--reinstall)
[ "$MODE" = keep ] && INSTALL_FLAGS+=(--no-download)
echo
echo "[$NAME] $PY dso_installer.py ${INSTALL_FLAGS[*]}"
echo
"$PY" "$HERE/dso_installer.py" "$@" "${INSTALL_FLAGS[@]}"
EXIT_CODE=$?
[ "$EXIT_CODE" -eq 0 ] || exit "$EXIT_CODE"
wants_help "$@" && exit 0

# The server's start.sh runs python3 unless DSO_PYTHON says otherwise.
START="bash $SERVER_DIR/start.sh"
[ "$PY" = python3 ] || START="DSO_PYTHON=$(printf '%q' "$PY") $START"
echo
echo "[$NAME] Ready. The server in $SERVER_DIR has the client's data; start it with"
echo "    $START"
echo "  Its README.md explains the settings (.env) for players on other machines."
exit 0
