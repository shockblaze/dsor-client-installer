@echo off
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem  Download the Drakensang Online client from Bigpoint's CDN and unpack it
rem  into a ready client folder (dso_installer.py), then build the launcher
rem  (launcher\dist\DSOLauncher.exe, with the DSOLauncher shortcut beside this
rem  script) pointed at that client.
rem
rem  Usage (double-clicking it is the first line):
rem    run.cmd                 install into dso-client\ beside this script
rem    run.cmd D:\Games\DSO    install into another folder (the first argument)
rem    run.cmd --reinstall     delete the installed client, install the current one
rem    run.cmd --launcher-only keep the installed client, set up the launcher
rem    run.cmd --help          the installer's options
rem
rem  Every argument is passed through to dso_installer.py; a relative folder is
rem  relative to this script's folder. When the folder already holds a client
rem  and neither --reinstall nor --launcher-only is given, it asks which. On a
rem  first install it offers to download the DSO Reconstruction server
rem  (DSO-Reconstruction/DSOR_Server) into DSOR_Server\ beside this script, and
rem  a server there without the client's data gets it (dso_installer.py
rem  --server-data) once the client is installed.
rem
rem  Python: the interpreter the DSO server runs on (Python 3.12 or newer,
rem  64-bit), found the way the server's run.bat finds it: py -3.12-64,
rem  py -3-64, then python. With none of them, this offers to install
rem  Python 3.12 from python.org for the current user (no administrator
rem  rights), where the server's run.bat finds it as well.
rem ---------------------------------------------------------------------------

rem Explorer starts the script in its folder, "Run as administrator" in
rem System32: always work beside the script.
pushd "%~dp0"

rem The last Python 3.12 release with a Windows installer, signed by the Python
rem Software Foundation.
set "PY_VERSION=3.12.10"
set "PY_INSTALLER=python-%PY_VERSION%-amd64.exe"
set "PY_URL=https://www.python.org/ftp/python/%PY_VERSION%/%PY_INSTALLER%"
set "PY_SHA256=67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
rem Where a per-user install goes. This console does not see the PATH entries
rem the installer adds until it is reopened, so it is also looked for here.
set "PY_HOME=%LOCALAPPDATA%\Programs\Python\Python312"

set "SERVER_REPO=DSO-Reconstruction/DSOR_Server"
set "SERVER_DIR=DSOR_Server"

rem Started from Explorer (cmd /c "...\run.cmd"): keep the window open at the end.
set "PAUSE_AT_END="
echo %cmdcmdline% | find /i "%~nx0" >nul && set "PAUSE_AT_END=1"
set "EXIT_CODE=1"

call :choose_mode %*
if "%MODE%"=="quit" goto finish
rem No client yet: asked now, before the long download.
if not defined MODE call :offer_server %*
rem A server here without the client's data gets it once the client is in
rem place; data it already has is left alone.
set "SERVER_DATA="
if exist "%SERVER_DIR%\data\" if not exist "%SERVER_DIR%\data\db\static.db4" set "SERVER_DATA=1"
call :find_python
if defined PY goto launch
call :install_python || goto finish
call :find_python
if defined PY goto launch
echo(
echo [run.cmd] ERROR: Python %PY_VERSION% was installed but cannot be found.
echo   Close this window and start run.cmd again.
goto finish

:launch
set "INSTALL_FLAGS="
if "%MODE%"=="reinstall" set "INSTALL_FLAGS=--reinstall"
if "%MODE%"=="keep" set "INSTALL_FLAGS=--launcher-only"
if defined SERVER_DATA set "INSTALL_FLAGS=%INSTALL_FLAGS% --server-data %SERVER_DIR%"
echo [run.cmd] %PY% dso_installer.py %INSTALL_FLAGS%
echo(
%PY% "%~dp0dso_installer.py" %* %INSTALL_FLAGS%
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" goto finish
rem --help prints the installer's options and installs nothing.
for %%A in (%*) do if /i "%%~A"=="--help" goto finish
for %%A in (%*) do if /i "%%~A"=="-h" goto finish

rem The installer has pointed the launcher at the client; build its programs
rem (launcher\dist) against that client, whose icon they take. A kept client
rem keeps a launcher that is already built.
if "%MODE%"=="keep" if exist "%~dp0launcher\dist\DSOLauncher.exe" if exist "%~dp0DSOLauncher.lnk" goto ready
echo(
echo [run.cmd] %PY% launcher\build_exe.py
echo(
%PY% "%~dp0launcher\build_exe.py"
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" goto finish
:ready
echo(
echo [run.cmd] Ready. Start DSOLauncher (the shortcut in this folder) to log in
echo   and play.
if defined SERVER_DATA echo   The server in %SERVER_DIR% has the client's data; its README.md explains how to start it.

:finish
popd
if not defined PAUSE_AT_END exit /b %EXIT_CODE%
echo(
pause
exit /b %EXIT_CODE%


rem --- An installed client -------------------------------------------------
:choose_mode
rem MODE: empty (install), reinstall, keep or quit. --reinstall and
rem --launcher-only on the command line answer the question, and --help
rem needs none.
set "MODE="
for %%A in (%*) do if /i "%%~A"=="--help" exit /b 0
for %%A in (%*) do if /i "%%~A"=="-h" exit /b 0
for %%A in (%*) do if /i "%%~A"=="--reinstall" set "MODE=reinstall"
for %%A in (%*) do if /i "%%~A"=="--launcher-only" set "MODE=keep"
if defined MODE exit /b 0

rem The folder is the first argument unless that is an option.
set "CLIENT_DIR=dso-client"
if "%~1"=="" goto client_dir_known
set "FIRST_ARG=%~1"
if not "%FIRST_ARG:~0,1%"=="-" set "CLIENT_DIR=%FIRST_ARG%"
:client_dir_known

rem A fast check: the export_win32 folder and one of the executables.
set "INSTALLED="
if exist "%CLIENT_DIR%\export_win32\" if exist "%CLIENT_DIR%\dro_client64.exe" set "INSTALLED=1"
if exist "%CLIENT_DIR%\export_win32\" if exist "%CLIENT_DIR%\dro_client.exe" set "INSTALLED=1"
if not defined INSTALLED exit /b 0

echo(
echo [run.cmd] A client is already installed in %CLIENT_DIR%.
echo(
echo   K  Keep it: download nothing, only set up the launcher.
echo   R  Reinstall: delete the installed client files, then download and
echo      install the current version (about 2 GB, 5 to 10 minutes).
echo(
choice /c KR /m "Keep or reinstall"
rem 255: no console to ask on.
if errorlevel 255 goto mode_unanswered
if errorlevel 2 goto mode_reinstall
set "MODE=keep"
exit /b 0
:mode_reinstall
set "MODE=reinstall"
exit /b 0
:mode_unanswered
echo   No answer could be read; nothing was changed. Pass --reinstall or
echo   --launcher-only to choose without being asked.
set "MODE=quit"
exit /b 0


rem --- The server -----------------------------------------------------------
:offer_server
rem Not for --help, nor when the server is already here.
for %%A in (%*) do if /i "%%~A"=="--help" exit /b 0
for %%A in (%*) do if /i "%%~A"=="-h" exit /b 0
if exist "%SERVER_DIR%\" exit /b 0
echo(
echo [run.cmd] The DSO Reconstruction server can be downloaded as well, from
echo   github.com/%SERVER_REPO% into %SERVER_DIR% beside this script
echo   (a few MB). It runs the game services the launcher logs in to.
echo(
choice /c YN /m "Download the server too"
rem 2: no; 255: no console to ask on.
if errorlevel 2 exit /b 0
echo(
echo [run.cmd] Downloading %SERVER_REPO% into %SERVER_DIR%
rem With git, a clone that git pull updates; without, the default branch as
rem an archive.
where git >nul 2>&1 || goto server_archive
set "GIT_TERMINAL_PROMPT=0"
git clone "https://github.com/%SERVER_REPO%.git" "%SERVER_DIR%" || goto server_failed
exit /b 0
:server_archive
set "SERVER_STAGE=.server-download"
if exist "%SERVER_STAGE%\" rmdir /s /q "%SERVER_STAGE%"
mkdir "%SERVER_STAGE%" || goto server_failed
call :download "https://github.com/%SERVER_REPO%/archive/HEAD.zip" "%SERVER_STAGE%\server.zip" || goto server_failed
call :unzip "%SERVER_STAGE%\server.zip" "%SERVER_STAGE%" || goto server_failed
del "%SERVER_STAGE%\server.zip"
rem The archive holds one folder, named after the repository and branch.
for /d %%D in ("%SERVER_STAGE%\*") do move "%%D" "%SERVER_DIR%" >nul
rmdir /s /q "%SERVER_STAGE%"
if not exist "%SERVER_DIR%\README.md" goto server_failed
exit /b 0
:server_failed
if defined SERVER_STAGE if exist "%SERVER_STAGE%\" rmdir /s /q "%SERVER_STAGE%"
echo(
echo [run.cmd] WARNING: the server could not be downloaded; the client install
echo   goes on. It is at https://github.com/%SERVER_REPO%
exit /b 0


rem --- Interpreter selection ------------------------------------------------
:find_python
set "PY="
call :try_python py -3.12-64
if not defined PY call :try_python py -3-64
if not defined PY call :try_python python
if not defined PY if exist "%PY_HOME%\python.exe" call :try_python "%PY_HOME%\python.exe"
exit /b 0

:try_python
rem dso_installer.py runs on 3.9 or newer; the server needs 3.12 or newer and
rem a 64-bit build, so one interpreter serves both. stdin is empty: a launcher
rem that would ask to install a missing version gets no answer.
%* -c "import lzma, ssl, struct, sys; sys.exit(0 if sys.version_info >= (3, 12) and struct.calcsize('P') == 8 else 1)" <nul >nul 2>&1
if not errorlevel 1 set "PY=%*"
exit /b 0


rem --- Python install -------------------------------------------------------
:install_python
echo(
echo [run.cmd] Python 3.12 or newer (64-bit) was not found.
echo(
echo   The client installer and the DSO server both run on it. Python
echo   %PY_VERSION% (64-bit) can be installed now from python.org, for your user
echo   account only: no administrator rights, about 100 MB.
echo(
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 goto install_32bit
choice /c YN /m "Install Python %PY_VERSION% now"
if errorlevel 2 goto install_declined

set "PY_DOWNLOAD=%TEMP%\%PY_INSTALLER%"
echo(
echo [run.cmd] Downloading %PY_URL%
call :download "%PY_URL%" "%PY_DOWNLOAD%" || goto download_failed

set "HASH="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%PY_DOWNLOAD%" SHA256 2^>nul') do if not defined HASH set "HASH=%%H"
if not defined HASH goto hash_failed
rem Older certutil versions print the digest in spaced pairs.
set "HASH=%HASH: =%"
if /i not "%HASH%"=="%PY_SHA256%" goto hash_failed

echo [run.cmd] Installing Python %PY_VERSION% for %USERNAME%
rem Per user, with the py launcher (py -3.12-64) and python on the PATH; the
rem window shows the progress and asks nothing.
start "" /wait "%PY_DOWNLOAD%" /passive InstallAllUsers=0 InstallLauncherAllUsers=0 Include_launcher=1 PrependPath=1 Include_test=0
set "PY_INSTALL_EXIT=%errorlevel%"
del "%PY_DOWNLOAD%" >nul 2>&1
rem 3010: installed, a restart is pending.
if "%PY_INSTALL_EXIT%"=="0" exit /b 0
if "%PY_INSTALL_EXIT%"=="3010" exit /b 0
echo(
echo [run.cmd] ERROR: the Python installer stopped with exit code %PY_INSTALL_EXIT%.
exit /b 1

:install_declined
echo(
echo   Nothing was installed. Install Python 3.12 or newer (64-bit) from
echo   https://www.python.org/downloads/windows/ and start run.cmd again.
exit /b 1

:install_32bit
echo   This is a 32-bit Windows; the DSO server needs a 64-bit one.
exit /b 1

:download_failed
del "%PY_DOWNLOAD%" >nul 2>&1
echo(
echo [run.cmd] ERROR: the download failed. Check the internet connection and
echo   start run.cmd again.
exit /b 1

:hash_failed
del "%PY_DOWNLOAD%" >nul 2>&1
echo(
echo [run.cmd] ERROR: the downloaded installer is not the expected file
echo   (SHA-256 %HASH%); it was deleted and nothing was installed.
exit /b 1


rem --- Helpers ---------------------------------------------------------------
:download
rem %1 to the file %2. curl and tar ship with Windows 10 1803 and later.
if not exist "%SystemRoot%\System32\curl.exe" goto download_powershell
"%SystemRoot%\System32\curl.exe" -fL --retry 3 -o "%~2" "%~1"
exit /b %errorlevel%
:download_powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; $ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2'"
exit /b %errorlevel%

:unzip
rem The archive %1 into the folder %2.
if not exist "%SystemRoot%\System32\tar.exe" goto unzip_powershell
"%SystemRoot%\System32\tar.exe" -xf "%~1" -C "%~2"
exit /b %errorlevel%
:unzip_powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%~1' -DestinationPath '%~2'"
exit /b %errorlevel%
