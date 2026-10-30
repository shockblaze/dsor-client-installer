"""Network launcher for the reconstructed Drakensang Online server.

Accounts live on the server, not here. Registering, logging in, refreshing a
session, and changing a password are all requests to the account service; this
tool holds only client-side settings and the session the service last issued.
The session GUID it receives is what the game client is started with, and what
the game services validate.

    python dso_client.py --server http://game.example.net:8080 register alice
    python dso_client.py login alice
    python dso_client.py launch alice
    python dso_client.py gui        # the same thing as a login window

The frozen ``dso-client.exe`` built by ``build_exe.py`` takes the same
commands. Settings and the cached session live in ``launcher-client.json``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from account_client import (  # noqa: E402
    DEFAULT_SERVER_URL,
    AccountServiceClient,
    AccountServiceError,
    ServiceUnreachableError,
    Session,
    normalize_server_url,
)
import dpapi  # noqa: E402


def application_directory() -> Path:
    """Directory the tool was started from, source tree or frozen exe alike."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_repository_root(start: Path) -> Path | None:
    """Walk up looking for the installer project that holds ``dso_installer.py``.

    The sources live in its ``launcher`` folder and the executables in
    ``launcher\\dist``, so both find the same root.
    """

    for directory in (start, *list(start.parents)[:4]):
        if (directory / "dso_installer.py").is_file():
            return directory
    return None


CLIENT_EXECUTABLES = ("dro_client64.exe", "dro_client.exe")


def client_executable(folder: Path) -> Path:
    """The game executable of an installed client folder.

    ``dro_client64.exe`` on 64-bit Windows, ``dro_client.exe`` on 32-bit
    Windows; when only one of the two is there, that one.
    """

    names = list(CLIENT_EXECUTABLES)
    if not platform.machine().endswith("64"):
        names.reverse()
    for name in names:
        if (Path(folder) / name).is_file():
            return Path(folder) / name
    return Path(folder) / names[0]


CLIENT_TEMP_DIRECTORY_NAME = "DSOClient"


def client_temp_directory() -> Path:
    """The ``DSOClient`` folder under ``%TEMP%``, the game client's scratch folder."""

    return Path(tempfile.gettempdir()) / CLIENT_TEMP_DIRECTORY_NAME


def ensure_client_temp_directory() -> Path | None:
    """Create the ``DSOClient`` folder under ``%TEMP%`` when it is missing.

    Called on startup by both front-ends. A failure is reported rather than
    raised: the launcher can still log in and start the game without it.
    """

    directory = client_temp_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"warning: could not create {directory}: {exc}", file=sys.stderr)
        return None
    return directory


APP_DIR = application_directory()
REPO_ROOT = find_repository_root(APP_DIR)
CONFIG_FILE = Path(
    os.getenv("DSO_LAUNCHER_CONFIG")
    or (REPO_ROOT or APP_DIR) / "launcher-client.json"
)

CONFIG_VERSION = 2
SESSION_GRACE_SECONDS = 60.0

LANGUAGES = ("de", "en", "fr", "pl", "es", "it", "pt", "tr", "ru")

# Where dso_installer.py extracts the client by default; an install elsewhere
# writes its own path into the settings.
DEFAULT_CLIENT_DIRECTORY = (REPO_ROOT or APP_DIR) / "dso-client"

DEFAULT_SETTINGS: dict[str, object] = {
    "server_url": DEFAULT_SERVER_URL,
    "client_path": str(client_executable(DEFAULT_CLIENT_DIRECTORY)),
    "language": "en",
    # Blank or zero means "use whatever the account service reports", which is
    # how a remote player gets the right address without configuring anything.
    "host_override": "",
    "gateway_port_override": 0,
    "chat_port_override": 0,
    "instance_id_override": 0,
    "last_username": "",
}
SETTING_ENVIRONMENT = {
    "server_url": "DSO_ACCOUNT_SERVICE_URL",
    "client_path": "DSO_CLIENT_PATH",
    "language": "DSO_LANGUAGE",
    "host_override": "DSO_HOST",
    "gateway_port_override": "DSO_GATEWAY_PORT",
    "chat_port_override": "DSO_CHAT_PORT",
    "instance_id_override": "DSO_CHAT_INSTANCE_ID",
}


class LauncherError(Exception):
    """A recoverable problem which is reported without a traceback."""


def _local_time(timestamp: float) -> str:
    if timestamp <= 0:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "expired"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


class ClientConfig:
    """Launcher settings, the last session the service handed out, and the
    passwords remembered for an account on an account service."""

    def __init__(self, path: Path = CONFIG_FILE) -> None:
        self.path = Path(path)
        self.settings: dict[str, object] = dict(DEFAULT_SETTINGS)
        self.session: Session | None = None
        # One entry per username and account service, the password encrypted
        # for the Windows user (see dpapi.py).
        self.passwords: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LauncherError(f"could not read {self.path}: {exc}")
        version = int(document.get("version", 0))
        if version > CONFIG_VERSION:
            raise LauncherError(
                f"{self.path} was written by a newer launcher (version "
                f"{version}); update this one or delete the file"
            )
        # Unknown keys are carried through: the window keeps its own state in
        # the same section.
        self.settings.update(document.get("settings", {}))
        cached = document.get("session")
        if isinstance(cached, dict):
            self.session = Session.from_document(cached)
        self.passwords = [
            entry
            for entry in document.get("passwords") or []
            if isinstance(entry, dict)
        ]

    def save(self) -> None:
        document = {
            "version": CONFIG_VERSION,
            "settings": self.settings,
            "session": self.session.to_document() if self.session else None,
            "passwords": self.passwords,
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.path)

    def setting(self, key: str) -> object:
        """Resolve a setting from the environment, the file, then defaults."""

        variable = SETTING_ENVIRONMENT.get(key)
        if variable and os.getenv(variable):
            return os.environ[variable]
        return self.settings.get(key, DEFAULT_SETTINGS[key])

    def client(self, *, server_url: str | None = None) -> AccountServiceClient:
        return AccountServiceClient(
            server_url or str(self.setting("server_url"))
        )

    def remember_server(self, url: str) -> None:
        """Keep the address an account was reached on.

        ``--server`` is otherwise per-invocation, which strands anyone who
        registers against one address and then runs a bare ``login``.
        """

        if url and str(self.settings.get("server_url")) != url:
            self.settings["server_url"] = url
            self.save()

    def store_session(self, session: Session) -> None:
        self.session = session
        self.settings["last_username"] = session.username
        self.save()

    def clear_session(self) -> None:
        self.session = None
        self.save()

    def cached_session(self, username: str | None = None) -> Session | None:
        """The stored session when it is still usable for ``username``."""

        session = self.session
        if session is None:
            return None
        if username and session.username.casefold() != username.casefold():
            return None
        if session.expires_at <= time.time() + SESSION_GRACE_SECONDS:
            return None
        return session

    def _password_key(
        self, username: str, server_url: str | None
    ) -> tuple[str, str]:
        """The account service and username a password is remembered under."""

        return (
            normalize_server_url(server_url or str(self.setting("server_url"))),
            username.strip().casefold(),
        )

    def remembered_password(
        self, username: str, *, server_url: str | None = None
    ) -> str | None:
        """The password remembered for ``username`` on the account service,
        the configured one unless ``server_url`` names another."""

        key = self._password_key(username, server_url)
        for entry in self.passwords:
            if _password_entry_key(entry) != key:
                continue
            try:
                blob = base64.b64decode(str(entry.get("password", "")), validate=True)
                return dpapi.unprotect(blob, _password_entropy(key)).decode("utf-8")
            except (binascii.Error, OSError, UnicodeDecodeError):
                # Another Windows user's, or edited by hand: unusable either way.
                return None
        return None

    def remember_password(
        self, username: str, password: str, *, server_url: str | None = None
    ) -> None:
        key = self._password_key(username, server_url)
        blob = dpapi.protect(password.encode("utf-8"), _password_entropy(key))
        self.passwords = [
            entry for entry in self.passwords if _password_entry_key(entry) != key
        ]
        self.passwords.append(
            {
                "server_url": key[0],
                "username": username.strip(),
                "password": base64.b64encode(blob).decode("ascii"),
            }
        )
        self.save()

    def forget_password(
        self, username: str, *, server_url: str | None = None
    ) -> None:
        key = self._password_key(username, server_url)
        kept = [entry for entry in self.passwords if _password_entry_key(entry) != key]
        if len(kept) != len(self.passwords):
            self.passwords = kept
            self.save()

    def resolved_username(self, username: str | None) -> str:
        candidate = (username or str(self.settings.get("last_username", ""))).strip()
        if not candidate:
            raise LauncherError(
                "no username given and none remembered; pass one explicitly"
            )
        return candidate


def _password_entry_key(entry: dict) -> tuple[str, str]:
    return (
        str(entry.get("server_url", "")),
        str(entry.get("username", "")).strip().casefold(),
    )


def _password_entropy(key: tuple[str, str]) -> bytes:
    # A remembered password decrypts only under the service and username it
    # was stored for, so editing the file cannot send it to another server.
    return "\n".join(key).encode("utf-8")


def prompt_password(prompt: str, supplied: str | None) -> str:
    if supplied is not None:
        return supplied
    if not sys.stdin.isatty():
        raise LauncherError("a password is required; pass --password")
    return getpass.getpass(prompt)


def resolve_client_path(config: ClientConfig, override: str | None) -> Path:
    raw = override or str(config.setting("client_path"))
    path = Path(raw).expanduser()
    if not path.is_file():
        raise LauncherError(
            f"client executable not found: {path}\n"
            f"install the client with run.cmd, pass --client-path, set "
            f"{SETTING_ENVIRONMENT['client_path']}, "
            f"or run: dso_client.py config --client-path <exe>"
        )
    return path.resolve()


def build_arguments(
    config: ClientConfig,
    session: Session,
    *,
    host: str | None = None,
    gateway_port: int | None = None,
    chat_port: int | None = None,
    instance_id: int | None = None,
    language: str | None = None,
) -> list[str]:
    """Build the client command line for a session issued by the service.

    The game client has two endpoints to be told about. ``-ip``/``-port`` is
    its primary one, the auth gateway that redirects it through login and
    into the map services; without them it silently dials 127.0.0.1:80,
    which is why a remote server used to accept logins and nothing else.
    ``-bica``/``-bicp`` is the built-in chat. Both use the one host the
    account service published.
    """

    host = (
        host
        or str(config.setting("host_override"))
        or session.host
        or "127.0.0.1"
    )
    gateway_port = int(
        gateway_port
        or int(config.setting("gateway_port_override") or 0)
        or session.gateway_port
    )
    chat_port = int(
        chat_port
        or int(config.setting("chat_port_override") or 0)
        or session.chat_port
    )
    instance_id = int(
        instance_id
        or int(config.setting("instance_id_override") or 0)
        or session.instance_id
    )
    language = language or str(config.setting("language"))
    if language not in LANGUAGES:
        raise LauncherError(
            f"language {language!r} is not one of {', '.join(LANGUAGES)}"
        )
    if not 0 < gateway_port <= 0xFFFF:
        raise LauncherError("gateway port must be a valid UDP port")
    if not 0 < chat_port <= 0xFFFF:
        raise LauncherError("chat port must be a valid UDP port")
    if not 0 < instance_id <= 0xFFFF_FFFF:
        raise LauncherError("instance id must be a non-zero uint32")
    return [
        "-accid", str(session.account_id),
        "-sessionId", session.session_id,
        "-sid", session.session_id,
        "-standalone",
        "-ip", host,
        "-port", str(gateway_port),
        "-bica", host,
        "-bicp", str(chat_port),
        "-instanceid", str(instance_id),
        "-language", language,
    ]


def quote_command(executable: Path, arguments: list[str]) -> str:
    command = [str(executable), *arguments]
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    import shlex

    return shlex.join(command)


def start_client(executable: Path, arguments: list[str]) -> int:
    """Start the client detached so it outlives this process."""

    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    process = subprocess.Popen(
        [str(executable), *arguments],
        cwd=str(executable.parent),
        close_fds=True,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    return process.pid


def authenticate(
    config: ClientConfig,
    client: AccountServiceClient,
    username: str,
    password: str | None,
    *,
    refresh: bool = False,
    allow_cached: bool = True,
) -> Session:
    """Obtain a usable session, reusing or refreshing the cached one first."""

    cached = config.cached_session(username) if allow_cached else None
    if cached is not None and not refresh:
        return cached
    if cached is not None and refresh:
        try:
            session = client.refresh(cached.session_id)
            config.store_session(session)
            return session
        except ServiceUnreachableError:
            raise
        except AccountServiceError:
            # Refused (expired, revoked, password changed): fall through to a
            # full login rather than failing the launch.
            pass
    password = prompt_password(f"Password for {username}: ", password)
    session = client.login(username, password)
    config.store_session(session)
    return session


def describe_session(session: Session) -> str:
    """Summarize a session without naming the server it belongs to.

    The address is deliberately absent here and from every routine message:
    this output lands in screenshots and pasted support threads, and where
    the server lives is not something a player needs to read.
    """

    remaining = format_duration(session.expires_at - time.time())
    return (
        f"  username    : {session.username}\n"
        f"  account id  : {session.account_id}\n"
        f"  session id  : {session.session_id} [{remaining}]\n"
        f"  expires at  : {_local_time(session.expires_at)}\n"
        f"  characters  : "
        + (
            ", ".join(str(value) for value in session.character_ids)
            if session.character_ids
            else "-"
        )
    )


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def command_register(config: ClientConfig, args: argparse.Namespace) -> int:
    client = config.client(server_url=args.server)
    password = args.password
    if password is None:
        password = prompt_password("Choose a password: ", None)
        if sys.stdin.isatty() and password != getpass.getpass("Repeat password: "):
            raise LauncherError("passwords do not match")
    account = client.register(args.username, password)
    config.remember_server(client.url)
    print(
        f"Registered {account.get('username', args.username)!r} as account "
        f"{account.get('account_id')}."
    )
    characters = account.get("character_ids") or []
    if characters:
        print(f"  characters  : {', '.join(str(value) for value in characters)}")
    if args.no_login:
        return 0
    session = client.login(args.username, password)
    config.store_session(session)
    print("Logged in:")
    print(describe_session(session))
    return 0


def command_login(config: ClientConfig, args: argparse.Namespace) -> int:
    client = config.client(server_url=args.server)
    username = config.resolved_username(args.username)
    session = authenticate(
        config,
        client,
        username,
        args.password,
        refresh=True,
        allow_cached=args.keep,
    )
    config.remember_server(client.url)
    print("Logged in:")
    print(describe_session(session))
    if args.print_args:
        print()
        print(" ".join(build_arguments(config, session)))
    return 0


def command_launch(config: ClientConfig, args: argparse.Namespace) -> int:
    client = config.client(server_url=args.server)
    username = config.resolved_username(args.username)
    try:
        session = authenticate(
            config, client, username, args.password, refresh=args.refresh
        )
    except ServiceUnreachableError as exc:
        cached = config.cached_session(username)
        if cached is None:
            raise
        # A live session outlives a brief outage of the account service; the
        # game services validate the GUID, not the launcher.
        print(f"warning: {exc}", file=sys.stderr)
        print(
            "warning: using the cached session, valid for "
            f"{format_duration(cached.expires_at - time.time())}",
            file=sys.stderr,
        )
        session = cached

    arguments = build_arguments(
        config,
        session,
        host=args.host,
        gateway_port=args.gateway_port,
        chat_port=args.chat_port,
        instance_id=args.instance_id,
        language=args.language,
    )
    executable = resolve_client_path(config, args.client_path)
    if args.dry_run:
        # Explicitly asked for: this is the one place the address is printed,
        # because the command line is what the flag exists to show.
        print(quote_command(executable, arguments))
        return 0
    pid = start_client(executable, arguments)
    print(
        f"Launched {session.username!r} (accid {session.account_id}) "
        f"as pid {pid}"
    )
    return 0


def command_logout(config: ClientConfig, args: argparse.Namespace) -> int:
    session = config.session
    if session is None:
        print("No session stored.")
        return 0
    client = config.client(server_url=args.server)
    try:
        revoked = client.logout(session.session_id)
    except ServiceUnreachableError as exc:
        config.clear_session()
        raise LauncherError(
            f"{exc}\nthe local session was cleared, but the server may still "
            "consider it valid"
        )
    config.clear_session()
    print(
        f"Logged out {session.username!r}"
        + ("." if revoked else "; the server had already ended that session.")
    )
    return 0


def command_session(config: ClientConfig, args: argparse.Namespace) -> int:
    session = config.session
    if session is None:
        print("No session stored. Run: dso_client.py login <username>")
        return 0
    print(describe_session(session))
    if session.expires_at <= time.time():
        print("\nThis session has expired; log in again.")
        return 0
    if args.verify:
        client = config.client(server_url=args.server)
        remote = client.validate(session.session_id)
        print(
            "\nThe server "
            + (
                "still accepts this session."
                if remote
                else "does NOT accept this session; log in again."
            )
        )
    return 0


def command_status(config: ClientConfig, args: argparse.Namespace) -> int:
    client = config.client(server_url=args.server)
    document = client.status()
    # The configured address is shown by "config", which is the settings
    # listing; a reachability report does not need to repeat it.
    print(f"Connected to {document.get('name') or 'the server'}")
    print(f"  service        : {document.get('service')} "
          f"v{document.get('version')}")
    print(f"  registration   : "
          f"{'open' if document.get('registration_open') else 'closed'}")
    print(f"  accounts       : {document.get('accounts')}")
    print(f"  active sessions: {document.get('active_sessions')}")
    print(
        f"  session life   : "
        f"{format_duration(float(document.get('session_ttl_seconds') or 0))}"
    )
    return 0


def command_characters(config: ClientConfig, args: argparse.Namespace) -> int:
    client = config.client(server_url=args.server)
    characters = client.characters()
    if not characters:
        print("The server published no characters.")
        return 0
    print(f"{'CHARACTER ID':>13}  {'NAME':<20} {'MAP':<22} OWNER")
    for character in characters:
        print(
            f"{int(character.get('character_id', 0)):>13}  "
            f"{str(character.get('name', '?')):<20} "
            f"{str(character.get('map_id', '-')):<22} "
            f"{character.get('owner') or '-'}"
        )
    return 0


def command_password(config: ClientConfig, args: argparse.Namespace) -> int:
    client = config.client(server_url=args.server)
    username = config.resolved_username(args.username)
    current = prompt_password(f"Current password for {username}: ", args.password)
    new_password = args.new_password
    if new_password is None:
        new_password = prompt_password("New password: ", None)
        if sys.stdin.isatty() and new_password != getpass.getpass("Repeat new password: "):
            raise LauncherError("passwords do not match")
    client.change_password(username, current, new_password)
    # The server ends every session issued under the old password.
    config.clear_session()
    if config.remembered_password(username, server_url=client.url) is not None:
        config.remember_password(username, new_password, server_url=client.url)
    print(f"Password changed for {username!r}; existing sessions were ended.")
    return 0


def command_gui(config: ClientConfig, args: argparse.Namespace) -> int:
    """Open the Tk login window against the same configuration."""

    try:
        from dso_launcher_gui import LauncherWindow
    except ImportError as exc:  # pragma: no cover - tkinter is optional
        raise LauncherError(f"the graphical launcher is unavailable: {exc}")
    LauncherWindow(config).mainloop()
    return 0


def command_config(config: ClientConfig, args: argparse.Namespace) -> int:
    updates = {
        "server_url": (
            normalize_server_url(args.server_url) if args.server_url else None
        ),
        "client_path": args.client_path,
        "language": args.language,
        "host_override": args.host,
        "gateway_port_override": args.gateway_port,
        "chat_port_override": args.chat_port,
        "instance_id_override": args.instance_id,
    }
    changed = {key: value for key, value in updates.items() if value is not None}
    if changed:
        config.settings.update(changed)
        config.save()
    print(f"Settings in {config.path}:")
    for key in DEFAULT_SETTINGS:
        if key == "last_username":
            continue
        effective = config.setting(key)
        variable = SETTING_ENVIRONMENT.get(key)
        source = ""
        if variable and os.getenv(variable):
            source = f"  (from ${variable})"
        shown = effective if effective not in ("", 0) else "(from the server)"
        print(f"  {key:<20} {shown}{source}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dso_client.py",
        description=(
            "Register, log in, and launch the DSO client against a server's "
            "account service."
        ),
    )
    parser.add_argument(
        "--config-file",
        default=str(CONFIG_FILE),
        help=f"launcher settings (default: {CONFIG_FILE.name})",
    )
    parser.add_argument(
        "--server",
        help="account service URL to use (default: the configured one); "
        "register and login remember it",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser(
        "register", help="create an account on the server"
    )
    register.add_argument("username")
    register.add_argument("--password", help="set the password without prompting")
    register.add_argument(
        "--no-login",
        action="store_true",
        help="register without logging in afterwards",
    )
    register.set_defaults(handler=command_register)

    login = subparsers.add_parser(
        "login", help="log in and store the issued session"
    )
    login.add_argument("username", nargs="?")
    login.add_argument("--password")
    login.add_argument(
        "--keep",
        action="store_true",
        help="keep the stored session when it is still valid",
    )
    login.add_argument(
        "--print-args",
        action="store_true",
        help="also print the resulting client arguments",
    )
    login.set_defaults(handler=command_login)

    launch = subparsers.add_parser(
        "launch", help="log in if needed and start the client executable"
    )
    launch.add_argument("username", nargs="?")
    launch.add_argument("--password")
    launch.add_argument(
        "--refresh",
        action="store_true",
        help="rotate the session before launching",
    )
    launch.add_argument("--client-path", help="client executable for this launch")
    launch.add_argument(
        "--host", help="override the server's game address (-ip and -bica)"
    )
    launch.add_argument(
        "--gateway-port", type=int, help="override the auth gateway port (-port)"
    )
    launch.add_argument("--chat-port", type=int, help="override -bicp")
    launch.add_argument("--instance-id", type=int, help="override -instanceid")
    launch.add_argument("--language", choices=LANGUAGES, help="-language")
    launch.add_argument(
        "--dry-run",
        action="store_true",
        help="print the command line instead of starting the client",
    )
    launch.set_defaults(handler=command_launch)

    logout = subparsers.add_parser(
        "logout", help="end the stored session on the server"
    )
    logout.set_defaults(handler=command_logout)

    session = subparsers.add_parser(
        "session", help="show the stored session"
    )
    session.add_argument(
        "--verify",
        action="store_true",
        help="ask the server whether it still accepts the session",
    )
    session.set_defaults(handler=command_session)

    status = subparsers.add_parser(
        "status", help="show what the account service reports"
    )
    status.set_defaults(handler=command_status)

    characters = subparsers.add_parser(
        "characters", help="show the server's roster and its owners"
    )
    characters.set_defaults(handler=command_characters)

    password = subparsers.add_parser(
        "password", help="change an account's password"
    )
    password.add_argument("username", nargs="?")
    password.add_argument("--password", help="the current password")
    password.add_argument("--new-password")
    password.set_defaults(handler=command_password)

    gui = subparsers.add_parser("gui", help="open the graphical launcher")
    gui.set_defaults(handler=command_gui)

    config = subparsers.add_parser(
        "config", help="show or change the launcher settings"
    )
    config.add_argument("--server-url", help="account service to use")
    config.add_argument("--client-path")
    config.add_argument("--language", choices=LANGUAGES)
    config.add_argument(
        "--host", help="override the server's game address (-ip and -bica)"
    )
    config.add_argument(
        "--gateway-port", type=int, help="override the auth gateway port (-port)"
    )
    config.add_argument("--chat-port", type=int)
    config.add_argument("--instance-id", type=int)
    config.set_defaults(handler=command_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_client_temp_directory()
    args = build_parser().parse_args(argv)
    try:
        config = ClientConfig(Path(args.config_file))
        return int(args.handler(config, args))
    except (LauncherError, AccountServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
