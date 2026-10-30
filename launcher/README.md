# launcher

The game launcher, merged from the DSO server's `dso_custom_client`: a client
for the server's login/register service that starts the game client
`dso_installer.py` extracted. Accounts live on the server,
not here: registering, logging in, refreshing a session, and changing a
password are all requests to `AccountService`
(`dso_server/services/accounts.py`). The session GUID the server issues is what
the game client is started with through `-sessionId`, and what the game
services validate.

| File | Purpose |
| --- | --- |
| `dso_client.py` | command line (`register`, `login`, `launch`, `logout`, `session`, `status`, `characters`, `password`, `config`, `gui`) |
| `dso_launcher_gui.py` | Tk login window over the same service |
| `account_client.py` | JSON-over-HTTP client for the account service |
| `dpapi.py` | Windows DPAPI encryption for remembered passwords |
| `build_exe.py` | freezes both front-ends into `dist` with PyInstaller |

Nothing outside the standard library is needed to *run* either front-end; only
the executable build pulls in PyInstaller.

## Running from source

```powershell
python .\dso_client.py config --server-url http://game.example.net:8080
python .\dso_client.py register alice
python .\dso_client.py launch alice
python .\dso_launcher_gui.py            # or: python .\dso_client.py gui
```

`--server` overrides the configured address for one invocation, which is the
quickest way to point at a different server. `launch --dry-run` prints the
command line instead of starting the client.

The game client is told about two endpoints, both on the host the login
response publishes: `-ip`/`-port` is its primary endpoint, the auth gateway
(UDP/80) that redirects it through login into the map services, and
`-bica`/`-bicp` is the built-in chat. Without `-ip`/`-port` the client dials
its built-in default of 127.0.0.1:80, which is why a remote server would accept
logins while every game service stayed unreachable. The overrides (Settings,
`config --host/--gateway-port/--chat-port`, `DSO_HOST`, `DSO_GATEWAY_PORT`,
`DSO_CHAT_PORT`) exist for servers that publish a wrong address.

Neither front-end displays the server's address in normal use. The window
shows the name the server publishes (`DSO_SERVER_NAME`), and messages and
errors have the address stripped out, so a screenshot or a stream does not
hand out a target. It is shown only where it is being configured: the Settings
dialog, `config`, and `launch --dry-run`, which exists to print the command
line.

The window opens on a loader while it probes the account service, then shows
the login form, or "Servers are currently down" with a retry when the service
cannot be reached — there is no form to fill in until the server answers.
Settings remain reachable in every state, so a wrong address can be corrected
from the offline screen. Characters are not listed here: they belong to the
account and appear in the game after logging in.

## Building the executables

`run.cmd` in the project root builds them once it has installed the client,
with the interpreter the DSO server runs on. By hand, use a 64-bit CPython 3.10
or newer (a 32-bit or MSYS2 interpreter cannot produce an ordinary Windows
executable):

```powershell
py -3.12-64 .\build_exe.py
```

An interpreter without PyInstaller builds from a virtual environment in
`.venv` beside the script: the first build creates it from that interpreter
and installs PyInstaller into it, so the interpreter's own packages (the
server's) stay as they are. That writes two single-file programs to `dist`:

- `DSOLauncher.exe` — the login window, built windowed (no console)
- `dso-client.exe` — the command line, identical to `dso_client.py`

and a shortcut to `DSOLauncher.exe`, `DSOLauncher.lnk`, in the project root.

Both borrow the configured game client's icon when that executable exists.
`--onedir` builds folders that start faster than the single-file form, and
`--icon` overrides the icon source.

## Sessions

The account service issues one live session per account: logging in again
anywhere ends the previous one, and changing a password ends all of them. The
launcher keeps the current session in `launcher-client.json` and reuses it
until it expires, so ordinary launches need no password. `login` and
`launch --refresh` rotate it; `logout` revokes it on the server.

If the account service is briefly unreachable but the stored session is still
valid, `launch` proceeds with a warning — the game services validate the GUID,
not the launcher. `session --verify` asks the server whether it still accepts
the stored session.

Two clients at once means two accounts, since a second login on one account
revokes the first session.

## Remembered passwords

"Remember password" under the password field keeps the password for that
username on that account service. Whenever the window's username and the
configured account service match a remembered pair again, the password is
filled in and the box is ticked; typing another username, or pointing
Settings at another service, takes the filled-in password back out, so one
account's password is never sent as another's. Names match ignoring case.

A password is remembered on a successful login with the box ticked, and
replaced when it is changed from the launcher (the window's dialog or the
`password` command). Unticking the box forgets the password for the username
in the field at once, even when that password no longer logs in. Logging out
ends the session and leaves a remembered password alone.

The password is encrypted with Windows DPAPI for the signed-in Windows user,
bound to the service address and username it was remembered for: another
Windows account, another machine, or an entry moved to a different server in
the file cannot decrypt it. Off Windows the box is not offered. Only the
window remembers passwords; the command line keeps reusing the session.

## Data files

`launcher-client.json` holds the launcher settings, the current session, and
any remembered passwords — those only DPAPI-encrypted, never in plain text
(see above). Treat the file as a credential anyway: the session GUID in it is
enough to start the game as that account until it expires or is revoked.

It is found by walking up from the script or executable to the project root
(the folder with `dso_installer.py`), so running from source and running
`dist\DSOLauncher.exe` share one file; a copy of the executable moved
elsewhere keeps its settings next to itself.

The client is the one `dso_installer.py` extracted: after every install it
sets `client_path` to that folder's `dro_client64.exe` on 64-bit Windows, or
`dro_client.exe` on 32-bit Windows, and leaves the other settings and the
session alone. Without a settings file the default is the same executable in
`dso-client` under the project root.
`DSO_LAUNCHER_CONFIG` overrides the location, and `DSO_ACCOUNT_SERVICE_URL`,
`DSO_CLIENT_PATH`, `DSO_LANGUAGE`, `DSO_HOST`, `DSO_GATEWAY_PORT`,
`DSO_CHAT_PORT`, and `DSO_CHAT_INSTANCE_ID` override individual settings.

Passwords cross the wire to the account service. Put it behind TLS before
exposing it beyond a trusted network.
