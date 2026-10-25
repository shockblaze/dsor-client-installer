#!/usr/bin/env python3
"""Install the Drakensang Online client from Bigpoint's public CDN.

    python dso_installer.py [<folder>] [--keep-cache] [--downloads N] [--cdn URL] ...

1. Download: the build's tables of contents and NB3 bundles from the CDN
   (<cdn>/__toc_human_readable.txt is the readable form of its main table; any
   file no bundle holds is fetched on its own), and the game's executables that
   <applet>/gamemanifest.xml lists. Every blob is checked against its md5, and
   an interrupted run resumes where it stopped.
2. Extract: simo8902's drakensang-nb3-bundle-extractor (neb.py, WTFPL) unpacks
   the bundles, laid out the way the game's own cache names them
   (<path with / as _>._<md5>).
3. Assemble: <folder>/export_win32 holds every file the tables list, the
   database at export_win32/db/static.db4, beside the executables, the DLLs,
   their .nz2 blobs and the two manifests.

The work files live in <folder>/.dso-installer and are removed once the
install is complete (--keep-cache keeps the downloads for the next run).
The launcher in launcher/ is then pointed at the installed client.
Python 3.9 or later, standard library only. On Windows, run.cmd finds the
Python the DSO server runs on, or installs it, and starts this script.
"""
import argparse
import contextlib
import hashlib
import http.client
import importlib.util
import io
import lzma
import os
import shutil
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path

CDN = "https://heredur.drakensang.com/cdndata"
APPLET = "https://heredur.drakensang.com/applet"
# The official thin client's user agent.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/538.1 (KHTML, like Gecko) DROThinClient/1.0 Safari/538.1"
TIMEOUT = 60

EXTRACTOR_REPO = "simo8902/drakensang-nb3-bundle-extractor"
# The extractor's internals are what this script drives (see extract()), so it
# is pinned to the commit it was tested with; --extractor-ref main takes the latest.
EXTRACTOR_REF = "c6097952a97cfee74ca4b5426b5d24fc5aff5a56"

# The two applications decode to the client's executables; every other applet
# blob is <file>.nz2.
APPLET_TARGETS = {"app.n3app.nz2": "dro_client.exe", "app64.n3app.nz2": "dro_client64.exe"}

# Measured on the 2026-10-25 build: 2.1 GB of cached bundles unpack to 4.72 GB.
UNPACK_RATIO = 2.4

CONSOLE = sys.stdout
TTY = CONSOLE.isatty()


class InstallError(Exception):
    pass


class NotFound(InstallError):
    pass


class Retryable(Exception):
    """A failure worth another attempt: a bad status, a damaged blob."""


def log(message):
    if Progress.open_line:
        print(file=CONSOLE)
        Progress.open_line = False
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=CONSOLE, flush=True)


def size_text(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def md5_hex(data):
    return hashlib.md5(data).hexdigest()


def write_atomic(path, data):
    """A file is either absent or complete, so a run cut short resumes cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)


# --- The CDN's containers ---------------------------------------------------

def unpack_zn(blob):
    """The stored form of every table, bundle and file: `__ZN` (or `ZN__`), the
    unpacked size (u32, little-endian), then a zlib stream. Anything else is
    stored as it is."""
    if len(blob) < 8 or blob[:4] not in (b"__ZN", b"ZN__"):
        return blob
    size = struct.unpack_from("<I", blob, 4)[0]
    try:
        raw = zlib.decompress(blob[8:])
    except zlib.error as e:
        raise InstallError(f"zlib stream: {e}") from e
    if len(raw) != size:
        raise InstallError(f"unpacked {len(raw)} bytes where the header says {size}")
    return raw


def parse_toc(blob, folder=""):
    """A table of contents: `path|d|md5` (a folder and its table's md5) or
    `path|f|md5` (a file and its stored blob's md5) per line."""
    entries = []
    for line in unpack_zn(blob).decode("latin-1").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or parts[1] not in ("d", "f") or len(parts[2].strip()) != 32:
            continue
        path = parts[0].strip().replace("\\", "/").strip("/")
        if "/" not in path and folder:
            path = f"{folder}/{path}"
        entries.append((path, parts[1], parts[2].strip().lower()))
    return entries


def logical_path(name):
    """A bundle member's name as the path the tables list: neb.py's cleanup,
    no `.nz`, and no `global/` before `export_win32/` (bundle633 stores the
    database as global/export_win32/db/static.db4)."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    path = "/".join(parts)
    if path.lower().endswith(".nz"):
        path = path[:-3]
    if path.lower().startswith("global/export_win32/"):
        path = path[len("global/"):]
    return path


def bundle_members(path):
    """(logical path, md5) of every member of an unpacked NB3 bundle: `_B3NHB3N`;
    the member count, 24, the offsets of the records and of the data (u32
    each); the names (u16 length, UTF-8); then a 44-byte record per member:
    its name's offset, the md5 of its stored blob (32 hex digits), its size and
    its offset."""
    with open(path, "rb") as f:
        magic, count, _names_at, records = struct.unpack("<8sIII", f.read(20))
        if magic != b"_B3NHB3N":
            raise InstallError(f"{path.name} is not an NB3 bundle")
        f.seek(24)
        table = f.read(records - 24)
        names, at = [], 0
        for _ in range(count):
            length = struct.unpack_from("<H", table, at)[0]
            names.append(table[at + 2:at + 2 + length].decode("utf-8", "replace"))
            at += 2 + length
        f.seek(records)
        info = f.read(44 * count)
    return [(logical_path(name), info[i * 44 + 4:i * 44 + 36].decode("ascii", "replace").lower())
            for i, name in enumerate(names)]


def decode_nz2(blob):
    """An applet blob: `_2ZN`, the LZMA properties (5 bytes), the decoded size
    (u32), then raw LZMA1 behind the x86 branch filter. The filter holds back
    the last few bytes, which it never changes, so those come from the stream
    decoded without it."""
    if len(blob) < 13 or blob[:4] != b"_2ZN":
        raise InstallError("not a _2ZN blob")
    props, dict_size, size = blob[4], *struct.unpack_from("<II", blob, 5)
    lzma1 = {"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
             "lc": props % 9, "lp": props // 9 % 5, "pb": props // 45}
    filtered = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_X86}, lzma1]).decompress(blob[13:])
    plain = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[lzma1]).decompress(blob[13:], max_length=size)
    if len(plain) != size or len(filtered) > size or size - len(filtered) > 4:
        raise InstallError(f"decoded {len(plain)} bytes where the header says {size}")
    return filtered + plain[len(filtered):]


# --- Downloading ------------------------------------------------------------

_ssl_context = None


def ssl_context():
    """The system's trusted roots. A Python that trusts none (MSYS2's builds
    with an empty ca-certificates bundle) takes them from the Windows
    certificate store, read through PowerShell."""
    global _ssl_context
    if _ssl_context is None:
        context = ssl.create_default_context()
        if not context.get_ca_certs() and sys.platform == "win32":
            script = ("foreach ($c in Get-ChildItem Cert:\\LocalMachine\\Root, Cert:\\CurrentUser\\Root) {"
                      " '-----BEGIN CERTIFICATE-----';"
                      " [Convert]::ToBase64String($c.RawData, 'InsertLineBreaks');"
                      " '-----END CERTIFICATE-----' }")
            try:
                pem = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                     capture_output=True, text=True, check=True, timeout=60).stdout
                context.load_verify_locations(cadata=pem)
            except (OSError, subprocess.SubprocessError, ssl.SSLError) as e:
                raise InstallError(f"this Python trusts no certificate authority, and the Windows store "
                                   f"could not be read ({e}); set SSL_CERT_FILE to a CA bundle") from e
        _ssl_context = context
    return _ssl_context


class Http:
    """GET over one kept-alive connection per thread and host."""

    def __init__(self):
        self.local = threading.local()

    def fetch(self, url, md5=None, size=None):
        """The body at `url`, checked against its md5 and size; a failure is
        retried three times, after a 1, 2 and 4 second pause."""
        for attempt in range(4):
            try:
                blob = self._get(url)
                if md5 is not None and md5_hex(blob) != md5:
                    raise Retryable(f"does not match its md5 {md5}")
                if size is not None and len(blob) != size:
                    raise Retryable(f"{len(blob)} bytes where {size} are listed")
                return blob
            except (OSError, http.client.HTTPException, Retryable) as e:
                if attempt == 3:
                    raise InstallError(f"{url}: {e}") from e
                time.sleep(2 ** attempt)

    def _get(self, url):
        parts = urllib.parse.urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        connections = self.local.__dict__.setdefault("connections", {})
        for stale_retry in (True, False):
            connection = connections.get(parts.netloc)
            reused = connection is not None
            if not reused:
                if parts.scheme == "https":
                    connection = http.client.HTTPSConnection(parts.netloc, timeout=TIMEOUT, context=ssl_context())
                else:
                    connection = http.client.HTTPConnection(parts.netloc, timeout=TIMEOUT)
                connections[parts.netloc] = connection
            try:
                connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
                response = connection.getresponse()
                body = response.read()
            except (OSError, http.client.HTTPException):
                connection.close()
                del connections[parts.netloc]
                if reused and stale_retry:
                    continue  # the server closed a kept-alive connection
                raise
            if response.status == 404:
                raise NotFound(f"{url}: not found")
            if response.status != 200:
                raise Retryable(f"HTTP {response.status}")
            return body


def cdn_url(cdn, path, md5):
    """Every table, bundle and file is at <path>.<md5>.nz; 13 paths have spaces."""
    return f"{cdn}/{urllib.parse.quote(path, safe=chr(39) + '/!$&()*+,;=:@')}.{md5}.nz"


def cache_name(path, md5):
    """The game's cache name for a file: its path with / as _, then ._<md5>."""
    return f"{path.replace('/', '_')}._{md5}"


class Progress:
    open_line = False  # a progress line on the terminal without its newline

    def __init__(self, label, total):
        self.label, self.total = label, total
        self.count = self.bytes = 0
        self.lock = threading.Lock()
        self.started = self.shown = time.monotonic()

    def add(self, nbytes=0):
        with self.lock:
            self.count += 1
            self.bytes += nbytes

    def show(self, final=False):
        now = time.monotonic()
        if not final and now - self.shown < (0.5 if TTY else 15):
            return
        self.shown = now
        line = f"  {self.label}: {self.count:,}/{self.total:,}"
        if self.bytes:
            rate = self.bytes / max(now - self.started, 1e-6)
            line += f"  {size_text(self.bytes)}  {size_text(rate)}/s"
        if TTY:
            print(f"\r{line:<72}", end="\n" if final else "", file=CONSOLE, flush=True)
            Progress.open_line = not final
        else:
            print(line, file=CONSOLE, flush=True)


def parallel(items, work, workers, progress):
    """work(item) for every item on `workers` threads; the results in any order.
    The first failure cancels what has not started and is raised."""
    results = []
    pool = ThreadPoolExecutor(workers)
    pending = {pool.submit(work, item) for item in items}
    try:
        while pending:
            # A timeout keeps Ctrl+C working on Windows.
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_EXCEPTION)
            results.extend(future.result() for future in done)
            progress.show()
        progress.show(final=True)
        return results
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


class Build:
    def __init__(self, key, folders, files):
        self.key = key
        self.folders = folders  # folder -> md5 of its table
        self.files = files      # path -> md5 of its stored blob

    @property
    def bundles(self):
        return {p: h for p, h in self.files.items() if p.startswith("bundles/")}

    @property
    def data(self):
        return {p: h for p, h in self.files.items() if p.startswith("export_win32/")}


def read_tables(http, cdn, input_dir, workers):
    """The current build: its key (`__root.txt`, the md5 of the main table),
    then every folder's table. The tables are written where neb.py looks for
    them: `__toc._<key>` and `<folder with / as _>___toc._<md5>`."""
    root = http.fetch(f"{cdn}/__root.txt").decode("utf-8-sig", "replace").strip()
    key = root.splitlines()[0].strip().lower() if root else ""
    if len(key) != 32:
        raise InstallError(f"{cdn}/__root.txt holds no build key")
    log(f"Build {key}")

    def table(path, md5):
        target = input_dir / cache_name(path, md5)
        if target.is_file():
            blob = target.read_bytes()
            if md5_hex(blob) == md5:
                return blob
        blob = http.fetch(cdn_url(cdn, path, md5), md5=md5)
        write_atomic(target, blob)
        return blob

    main = table("__toc", key)
    folders, files = {}, {}
    pending = {p: h for p, kind, h in parse_toc(main) if kind == "d"}
    progress = Progress("tables", len(pending))

    def folder_table(item):
        folder, md5 = item
        entries = parse_toc(table(f"{folder}/__toc", md5), folder)
        progress.add()
        return entries

    # The main table lists every folder; a folder's own table may list more.
    while pending:
        folders.update(pending)
        progress.total = len(folders)
        found = {}
        for entries in parallel(pending.items(), folder_table, workers, progress):
            for path, kind, md5 in entries:
                if kind == "f":
                    files[path] = md5
                elif path not in folders:
                    found[path] = md5
        pending = found
    build = Build(key, folders, files)
    log(f"{len(folders):,} folders, {len(build.data):,} files in {len(build.bundles):,} bundles")
    return build


def download_bundles(http, cdn, build, input_dir, workers):
    """Every bundle the tables list, unpacked from its `__ZN` blob, as neb.py
    reads them: `bundles_optional_bundle12.nb._<md5>`."""
    bundles = build.bundles
    todo = [(p, h) for p, h in bundles.items() if not (input_dir / cache_name(p, h)).is_file()]
    if len(todo) < len(bundles):
        log(f"{len(bundles) - len(todo):,} bundles already downloaded")
    progress = Progress("bundles", len(todo))

    def fetch(item):
        path, md5 = item
        blob = http.fetch(cdn_url(cdn, path, md5), md5=md5)
        raw = unpack_zn(blob)
        if raw[:8] != b"_B3NHB3N":
            raise InstallError(f"{path} is not an NB3 bundle")
        write_atomic(input_dir / cache_name(path, md5), raw)
        progress.add(len(blob))

    parallel(todo, fetch, workers, progress)


def download_loose(http, cdn, build, input_dir, workers):
    """The files no bundle holds at the md5 the tables list, each fetched on
    its own and kept packed: neb.py unpacks `<path with / as _>._<md5>`."""
    held = set()
    for path, md5 in build.bundles.items():
        held.update(bundle_members(input_dir / cache_name(path, md5)))
    loose = [(p, h) for p, h in build.data.items() if (p, h) not in held]
    todo = [(p, h) for p, h in loose if not (input_dir / cache_name(p, h)).is_file()]
    if not loose:
        return
    log(f"{len(loose):,} files are in no bundle; fetching them on their own")
    progress = Progress("files", len(todo))

    def fetch(item):
        path, md5 = item
        blob = http.fetch(cdn_url(cdn, path, md5), md5=md5)
        write_atomic(input_dir / cache_name(path, md5), blob)
        progress.add(len(blob))

    parallel(todo, fetch, workers, progress)


def prune_input(build, input_dir):
    """Only this build's tables, bundles and files go to the extractor: a
    cache left by another build would add paths the tables do not list."""
    keep = {cache_name("__toc", build.key)}
    keep.update(cache_name(f"{folder}/__toc", md5) for folder, md5 in build.folders.items())
    keep.update(cache_name(path, md5) for path, md5 in build.files.items())
    removed = 0
    for entry in os.scandir(input_dir):
        if entry.is_file() and entry.name not in keep:
            os.remove(entry.path)
            removed += 1
    if removed:
        log(f"Removed {removed:,} cached files of another build")


def download_applet(http, applet, applet_dir):
    """The game's executables and DLLs: <applet>/gamemanifest.xml lists each
    `<name>.nz2` with its size and md5, served at <applet>/<name>.<md5>.nz2."""
    manifests = {name: http.fetch(f"{applet}/{name}") for name in ("gamemanifest.xml", "manifest2.xml")}
    files = [(f.get("name"), int(f.get("size")), f.get("md5").lower())
             for f in ElementTree.fromstring(manifests["gamemanifest.xml"]).iter("File")]
    if not files:
        raise InstallError(f"{applet}/gamemanifest.xml lists no files")
    applet_dir.mkdir(parents=True, exist_ok=True)
    for name, data in manifests.items():
        write_atomic(applet_dir / name, data)
    progress = Progress("applet", len(files))

    def fetch(item):
        name, size, md5 = item
        target = applet_dir / name
        if not (target.is_file() and target.stat().st_size == size and md5_hex(target.read_bytes()) == md5):
            stem = name[:-len(".nz2")] if name.endswith(".nz2") else name
            blob = http.fetch(f"{applet}/{urllib.parse.quote(stem)}.{md5}.nz2", md5=md5, size=size)
            write_atomic(target, blob)
        progress.add(size)

    parallel(files, fetch, 8, progress)
    return [name for name, _size, _md5 in files] + list(manifests)


# --- Extracting -------------------------------------------------------------

def extractor_path(args, work):
    """neb.py: a local checkout (--extractor), or the pinned commit from GitHub."""
    if args.extractor:
        path = Path(args.extractor)
        path = path / "neb.py" if path.is_dir() else path
        if not path.is_file():
            raise InstallError(f"no neb.py at {args.extractor}")
        return path
    folder = work / "extractor" / args.extractor_ref
    if not (folder / "neb.py").is_file():
        url = f"https://github.com/{EXTRACTOR_REPO}/archive/{args.extractor_ref}.zip"
        log(f"Fetching the extractor ({url})")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=ssl_context()) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        folder.mkdir(parents=True, exist_ok=True)
        for member in archive.namelist():
            name = member.split("/", 1)[-1]
            if name in ("neb.py", "LICENSE.MD", "README.md"):
                (folder / name).write_bytes(archive.read(member))
        if not (folder / "neb.py").is_file():
            raise InstallError(f"{url} holds no neb.py")
    return folder / "neb.py"


def extract(neb_path, build, input_dir, output_dir, log_path):
    """Run neb.py over the cache: it reads the build's tables (--rootkey),
    unpacks every bundle member the tables list at that md5, places the loose
    files by md5 and copies a file to every further path that lists its md5.
    Its own log goes to `log_path`."""
    os.environ["NO_COLOR"] = "1"
    spec = importlib.util.spec_from_file_location("neb", neb_path)
    neb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(neb)
    neb.INPUT_ROOT, neb.OUTPUT_ROOT = str(input_dir), str(output_dir)
    neb.DEBUG = False
    # The game reads the .pbxml packs (sequences/, quests/) as they are; the
    # client tree has no loose .bxml beside them.
    neb.extract_bxml = lambda path, rel: False

    inputs = sum(1 for e in os.scandir(input_dir) if e.is_file())
    progress = Progress("extracting", inputs)
    handle_file = neb.handle_file

    def handle_with_progress(path, rel):
        try:
            return handle_file(path, rel)
        finally:
            progress.add(os.path.getsize(path))
            progress.show()

    neb.handle_file = handle_with_progress
    argv = sys.argv
    sys.argv = [str(neb_path), "--rootkey", build.key]
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as out, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            neb.main()
    finally:
        sys.argv = argv
    progress.show(final=True)


def complete_tree(build, output_dir):
    """Check the extracted export_win32 against the tables. A listed file
    still missing is taken from where neb.py staged a member no table folder
    names (__bundle_members/<md5>/..., or global/export_win32/...); files no
    table lists are removed."""
    tree = output_dir / "export_win32"
    present = {}
    for root, _dirs, names in os.walk(tree):
        for name in names:
            path = Path(root, name)
            present[path.relative_to(output_dir).as_posix().lower()] = path
    data = build.data
    missing = [p for p in data if p.lower() not in present]

    if missing:
        staged = {}
        stage = output_dir / "__bundle_members"
        if stage.is_dir():
            for md5_dir in stage.iterdir():
                for path in md5_dir.rglob("*"):
                    if path.is_file():
                        staged.setdefault(md5_dir.name.lower(), path)
        still = []
        for p in missing:
            source = staged.get(data[p])
            if source is None and (output_dir / "global" / p).is_file():
                source = output_dir / "global" / p
            if source is None:
                still.append(p)
                continue
            target = output_dir / p
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            present[p.lower()] = target
        missing = still
    if missing:
        sample = "\n".join(f"  {p}" for p in sorted(missing)[:20])
        raise InstallError(f"{len(missing):,} listed files were not extracted:\n{sample}")

    listed = {p.lower() for p in data}
    extra = [path for key, path in present.items() if key not in listed]
    for path in extra:
        path.unlink()
    for root, _dirs, _names in os.walk(tree, topdown=False):
        if Path(root) != tree:
            with contextlib.suppress(OSError):
                os.rmdir(root)  # only an empty folder goes
    if extra:
        log(f"Removed {len(extra):,} extracted files the tables do not list")
    log(f"export_win32 complete: {len(data):,} files")


# --- Assembling -------------------------------------------------------------

def install(dest, output_dir, work, applet_dir, applet_files):
    """export_win32 goes in whole (an older one is replaced once the new one is
    in place), then the applet blobs, their decoded executables and DLLs, and
    the manifests."""
    tree, old = dest / "export_win32", work / "export_win32.old"
    if old.exists():
        shutil.rmtree(old)
    if tree.exists():
        shutil.move(tree, old)
    shutil.move(output_dir / "export_win32", tree)
    if old.exists():
        shutil.rmtree(old)

    for name in applet_files:
        blob = (applet_dir / name).read_bytes()
        if name.endswith(".nz2"):
            target = APPLET_TARGETS.get(name, name[:-len(".nz2")])
            write_atomic(dest / target, decode_nz2(blob))
        write_atomic(dest / name, blob)


def configure_launcher(dest):
    """Point the launcher in launcher/ beside this script at the installed
    client: dro_client64.exe on 64-bit Windows, dro_client.exe otherwise. Its
    other settings and its session are kept."""
    source = Path(__file__).resolve().parent / "launcher" / "dso_client.py"
    if not source.is_file():
        return
    spec = importlib.util.spec_from_file_location("dso_client", source)
    launcher = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(launcher)
        config = launcher.ClientConfig()
        executable = launcher.client_executable(dest)
        config.settings["client_path"] = str(executable)
        config.save()
    except Exception as e:  # the client is installed either way
        log(f"Could not update the launcher's settings: {e}")
        return
    log(f"Launcher set to {executable} ({config.path})")


def remove_client(dest):
    """Delete what an install wrote to <folder>: export_win32, the download
    cache, the applet blobs with the executables and DLLs they decode to, and
    the manifests. Anything else in the folder stays."""
    if not dest.is_dir():
        return
    names = {"gamemanifest.xml", "manifest2.xml", *APPLET_TARGETS.values()}
    for blob in dest.glob("*.nz2"):
        names.update((blob.name, APPLET_TARGETS.get(blob.name, blob.name[:-len(".nz2")])))
    log(f"Removing the installed client in {dest}")
    for name in (".dso-installer", "export_win32"):
        if (dest / name).is_dir():
            shutil.rmtree(dest / name)
    for name in names:
        with contextlib.suppress(FileNotFoundError):
            (dest / name).unlink()


def free_space_check(input_dir, path):
    cached = sum(e.stat().st_size for e in os.scandir(input_dir) if e.is_file())
    needed = int(cached * UNPACK_RATIO) + (256 << 20)
    free = shutil.disk_usage(path).free
    if free < needed:
        raise InstallError(f"extracting needs about {size_text(needed)} free on {path.anchor or path}; "
                           f"{size_text(free)} is free")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Download the Drakensang Online client from Bigpoint's CDN "
                                                 "and unpack its bundles into a ready client folder.")
    parser.add_argument("folder", nargs="?", default="dso-client",
                        help="the client folder to create or update (default: ./dso-client)")
    parser.add_argument("--cdn", default=CDN, help=f"the data CDN (default: {CDN})")
    parser.add_argument("--applet", default=APPLET, help=f"where the executables are served (default: {APPLET})")
    parser.add_argument("--downloads", type=int, default=16,
                        help="parallel bundle downloads; tables and single files use twice as many (default: 16)")
    parser.add_argument("--work", help="the work folder (default: <folder>/.dso-installer)")
    parser.add_argument("--keep-cache", action="store_true",
                        help="keep the downloaded tables and bundles in the work folder for the next run")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reinstall", action="store_true",
                      help="first delete the client files already in the folder (export_win32, the executables, "
                           "DLLs and .nz2 blobs, the manifests, the download cache), then install the current version")
    mode.add_argument("--launcher-only", action="store_true",
                      help="download nothing: point the launcher at the client already in the folder")
    parser.add_argument("--extractor", help="a local drakensang-nb3-bundle-extractor checkout (or its neb.py)")
    parser.add_argument("--extractor-ref", default=EXTRACTOR_REF,
                        help=f"the extractor commit or branch to fetch from GitHub (default: {EXTRACTOR_REF[:12]})")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    dest = Path(args.folder).resolve()
    work = Path(args.work).resolve() if args.work else dest / ".dso-installer"
    input_dir, output_dir, applet_dir = work / "input", work / "output", work / "applet"
    if args.launcher_only:
        if not (dest / "export_win32").is_dir():
            raise InstallError(f"no client is installed in {dest}")
        configure_launcher(dest)
        return
    cdn, applet = args.cdn.rstrip("/"), args.applet.rstrip("/")
    started = time.monotonic()
    http = Http()
    if args.reinstall:
        # Offline, the installed client stays rather than leaving none.
        http.fetch(f"{cdn}/__root.txt")
        http.fetch(f"{applet}/gamemanifest.xml")
        remove_client(dest)
    input_dir.mkdir(parents=True, exist_ok=True)

    log(f"Installing into {dest}")
    neb_path = extractor_path(args, work)
    build = read_tables(http, cdn, input_dir, 2 * args.downloads)
    applet_files = download_applet(http, applet, applet_dir)
    download_bundles(http, cdn, build, input_dir, args.downloads)
    download_loose(http, cdn, build, input_dir, 2 * args.downloads)
    prune_input(build, input_dir)

    free_space_check(input_dir, work)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    log(f"Extracting with {neb_path} (its log: {work / 'extract.log'})")
    extract(neb_path, build, input_dir, output_dir, work / "extract.log")
    complete_tree(build, output_dir)

    install(dest, output_dir, work, applet_dir, applet_files)
    configure_launcher(dest)
    if args.keep_cache:
        shutil.rmtree(output_dir, ignore_errors=True)
        log(f"Kept the downloads in {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    minutes, seconds = divmod(int(time.monotonic() - started), 60)
    log(f"Done in {minutes} min {seconds} s: {dest}")


if __name__ == "__main__":
    try:
        main()
    except (InstallError, OSError) as e:
        log(f"Failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("Interrupted; run again to resume")
        sys.exit(130)
