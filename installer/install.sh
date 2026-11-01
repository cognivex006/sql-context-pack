#!/bin/sh
# SQL Context Pack one-line setup for Linux and macOS.
#
#   curl --progress-bar -fL https://github.com/cognivex006/sql-context-pack/releases/latest/download/install.sh | sh
#
# Downloads the latest release bundle, verifies its SHA-256, unpacks it under
# ${XDG_DATA_HOME:-~/.local/share}/sql-context-pack/releases/<version>, runs that release's own
# install.sh for each selected host, and then checks package, plugin and service versions.
#
# install.sh and update.sh differ only in OPERATION; a contract test keeps them identical
# otherwise.
#
# SQLCTX_HOST=codex,claude  limits the hosts (codex, claude, gemini, agy).
# SQLCTX_DRY_RUN=1          downloads and verifies the release, prints the plan, changes nothing.
set -eu
OPERATION=install

# The same order as the release's install.sh, so both select one interpreter.
PYTHON=
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -I -B -c 'import sys, importlib.util as u; sys.exit(sys.version_info < (3, 11) or u.find_spec("pip") is None)' >/dev/null 2>&1; then
        PYTHON=$candidate
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo 'SQL Context Pack needs Python 3.11 or newer with pip on PATH. Install it, then run this again.' >&2
    exit 1
fi
command -v bash >/dev/null 2>&1 || { echo 'SQL Context Pack setup needs bash.' >&2; exit 1; }

"$PYTHON" -I -B - "$OPERATION" <<'PY'
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

OPERATION = sys.argv[1]
REPOSITORY_URL = 'https://github.com/cognivex006/sql-context-pack'
BUNDLE = 'sql-context-pack-install.zip'
HOSTS = ('codex', 'claude', 'gemini', 'agy')
HOME = Path.home()
DATA_HOME = Path(os.environ.get('XDG_DATA_HOME') or HOME / '.local' / 'share')
RELEASES = DATA_HOME / 'sql-context-pack' / 'releases'
LOGS = DATA_HOME / 'sql-context-pack' / 'logs'
DRY_RUN = os.environ.get('SQLCTX_DRY_RUN') == '1'


def cells(text, limit):
    """Display-cell clipping that never forwards terminal controls from untrusted text."""
    result, used = [], 0
    for char in str(text):
        category = unicodedata.category(char)
        if category.startswith('C'):
            char, width = ' ', 1
        else:
            width = 0 if category.startswith('M') else 2 if unicodedata.east_asian_width(char) in 'WF' else 1
        if used + width > limit:
            break
        result.append(char)
        used += width
    return ''.join(result)


def size(count):
    return f'{count / 1048576:.1f} MB' if count >= 1048576 else f'{count // 1024} KB'


class Display:
    """Two reserved terminal rows: an overall bar, then a spinner with the current stage.

    A heartbeat keeps the spinner moving while a child installer is silent.
    """

    def __init__(self, operation):
        self.stream = sys.stderr
        self.interactive = self.stream.isatty() and os.environ.get('TERM') != 'dumb'
        self.operation = operation
        self.percent, self.stage, self.detail = 0, 'Starting', ''
        self.received, self.total = None, None
        self.started = time.monotonic()
        self.tick = self.printed = 0
        self.last_printed = None
        self.active = self.closed = False
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker = None
        if self.interactive:
            self.worker = threading.Thread(target=self._heartbeat, daemon=True)
            self.worker.start()

    def _heartbeat(self):
        while not self.stop.wait(0.2):
            with self.lock:
                self._draw()

    def _draw(self):
        if self.closed:
            return
        if not self.interactive:
            if self.stage != self.last_printed and self.printed < 40:
                print(f'sql-context-pack {self.operation}: {cells(self.stage, 160)}', file=self.stream, flush=True)
                self.last_printed = self.stage
                self.printed += 1
            return
        width = max(20, shutil.get_terminal_size((80, 24)).columns - 1)
        downloading = self.received is not None
        shown = (min(100, self.received * 100 // self.total) if self.total else None) if downloading else self.percent
        label = 'Downloading' if downloading else f'sql-context-pack {self.operation}'
        suffix = f' {shown}%' if shown is not None else ''
        if downloading:
            suffix += f'  {size(self.received)}' + (f' / {size(self.total)}' if self.total else '')
        bar_width = min(30, max(10, width - len(label) - len(suffix) - 3))
        filled = shown * bar_width // 100 if shown is not None else self.tick % bar_width
        bar = ('#' * filled + '-' * (bar_width - filled) if shown is not None
               else '-' * filled + '#' + '-' * (bar_width - filled - 1))
        spinner = '|/-' + chr(92)
        elapsed = time.monotonic() - self.started
        bottom = f'{spinner[self.tick % 4]} {elapsed:.1f}s  {self.stage}' + (f' - {self.detail}' if self.detail else '')
        self.tick += 1
        if not self.active:
            self.stream.write('\x1b[?25l\r\n')
            self.active = True
        # The cursor always rests on the second reserved row, at column one.
        self.stream.write('\r\x1b[1A\x1b[2K' + cells(f'{label} [{bar}]{suffix}', width)
                          + '\r\n\x1b[2K' + cells(bottom, width) + '\r')
        self.stream.flush()

    def update(self, percent, stage):
        with self.lock:
            if stage != self.stage:
                self.started, self.detail = time.monotonic(), ''
            self.percent = max(self.percent, max(0, min(100, int(percent))))
            self.stage, self.received, self.total = stage, None, None
            self._draw()

    def note(self, detail):
        with self.lock:
            self.detail = detail

    def transfer(self, received, total, stage):
        with self.lock:
            changed = stage != self.stage or self.received is None
            if changed:
                self.started, self.detail = time.monotonic(), ''
            self.stage, self.received, self.total = stage, received, total if total and total > 0 else None
            if changed:
                self._draw()

    def close(self):
        self.stop.set()
        if self.worker is not None:
            self.worker.join()
        with self.lock:
            if self.active:
                self.stream.write('\r\x1b[2K\x1b[1A\r\x1b[2K\x1b[?25h')
                self.stream.flush()
                self.active = False
            self.closed = True


ui = Display(OPERATION)
state = {'stage': 'Starting', 'log': None, 'log_path': None, 'tail': []}


class SetupError(RuntimeError):
    pass


def step(percent, stage):
    state['stage'] = stage
    ui.update(percent, stage)


def log(line):
    if state['log'] is not None:
        state['log'].write(line + '\n')
        state['log'].flush()
    state['tail'] = (state['tail'] + [line])[-20:]


def destination(host):
    if host == 'gemini':
        return HOME / '.gemini' / 'extensions' / 'sql-context-pack'
    if host == 'agy':
        return HOME / '.gemini' / 'config' / 'skills' / 'sql-context-pack'
    return HOME / 'plugins' / 'sql-context-pack'


def registrations(host):
    """Plugin ids this host already lists sql-context-pack under, read from its own state files.

    `codex plugin list` can take over a minute to answer, which is too slow here.
    """
    try:
        if host == 'codex':
            config = Path(os.environ.get('CODEX_HOME') or HOME / '.codex') / 'config.toml'
            text = config.read_text(encoding='utf-8', errors='replace') if config.is_file() else ''
            ids = re.findall(r'''^\s*\[plugins\.["'](sql-context-pack@[^"']+)["']\]''', text, re.MULTILINE)
        elif host == 'claude':
            installed = Path(os.environ.get('CLAUDE_CONFIG_DIR') or HOME / '.claude') / 'plugins' / 'installed_plugins.json'
            document = json.loads(installed.read_text(encoding='utf-8')) if installed.is_file() else {}
            plugins = document.get('plugins', {}) if isinstance(document, dict) else {}
            ids = [key for key in plugins if isinstance(key, str) and key.startswith('sql-context-pack@')]
        else:
            ids = []
    except (OSError, ValueError):
        ids = []
    return list(dict.fromkeys(ids))


def installed(host):
    return bool(registrations(host)) if host in ('codex', 'claude') else destination(host).exists()


def select_hosts():
    requested = os.environ.get('SQLCTX_HOST')
    if requested:
        selected = list(dict.fromkeys(item for item in re.split(r'[\s,;]+', requested.lower()) if item))
        if not selected or any(item not in HOSTS for item in selected):
            raise SetupError(f"SQLCTX_HOST must name codex, claude, gemini or agy; it is '{requested}'.")
        return selected
    present = [host for host in HOSTS if installed(host)]
    if OPERATION == 'update':
        if not present:
            raise SetupError('SQL Context Pack is not installed for codex, claude, gemini or agy. '
                             'Run install.sh first, or set SQLCTX_HOST.')
        return present
    found = [host for host in HOSTS if host in present or shutil.which(host)]
    if not found:
        raise SetupError('No supported host CLI (codex, claude, agy or gemini) is on PATH. '
                         'Install one, or set SQLCTX_HOST, then run this again.')
    return found


def plan(host):
    # A registration from another marketplace (the GitHub marketplace, or a developer's local
    # checkout) stays the owner's: only the package and service are installed for it, so the
    # host never lists sql-context-pack twice.
    foreign = [item for item in registrations(host) if item != 'sql-context-pack@personal']
    mode = 'native' if foreign and host != 'agy' else 'personal'
    registration = foreign[0] if foreign else 'sql-context-pack@personal' if host in ('codex', 'claude') else None
    return {'host': host, 'mode': mode, 'registration': registration, 'update': destination(host).exists()}


def refresh_command(item):
    marketplace = item['registration'].split('@', 1)[1]
    if item['host'] == 'codex':
        return f'codex plugin marketplace upgrade {marketplace}'
    return f"claude plugin marketplace update {marketplace}; claude plugin install {item['registration']}"


def request(url):
    return urllib.request.Request(url, headers={'User-Agent': 'sql-context-pack-setup'})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def latest_tag():
    # Pin one tag first so the bundle and its checksum always come from the same release.
    try:
        with urllib.request.build_opener(_NoRedirect).open(request(REPOSITORY_URL + '/releases/latest'), timeout=60) as response:
            location = response.headers.get('Location', '')
    except urllib.error.HTTPError as error:
        location = error.headers.get('Location', '') if 300 <= error.code < 400 else ''
    match = re.search(r'/releases/tag/(v\d+\.\d+\.\d+)$', location or '')
    if not match:
        raise SetupError('No published SQL Context Pack release was found.')
    return match[1]


def download(url, target, limit, label):
    ui.transfer(0, None, label)
    try:
        response = urllib.request.urlopen(request(url), timeout=60)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise SetupError(f'The latest release has no {label} asset yet. Existing installation was not changed.') from None
        raise
    with response, target.open('xb') as output:
        total = int(response.headers.get('Content-Length') or 0)
        if total > limit:
            raise SetupError(f'{label} is larger than the setup limit.')
        received = 0
        while block := response.read(65536):
            received += len(block)
            if received > limit:
                raise SetupError(f'{label} is larger than the setup limit.')
            output.write(block)
            ui.transfer(received, total, label)
        if total and received != total:
            raise SetupError(f'{label} ended before all declared bytes arrived.')


def check_bundle(archive):
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 20000:
            raise SetupError('The release bundle has too many entries.')
        for entry in entries:
            name = entry.filename
            if '\\' in name or ':' in name or name.startswith('/') or '..' in name.split('/'):
                raise SetupError('The release bundle contains an unsafe path.')
        if sum(entry.file_size for entry in entries) > 536870912:
            raise SetupError('The release bundle expands beyond the setup limit.')


def assert_release(root, version):
    for relative in ('install.sh', 'scripts/global_install.py', 'scripts/bootstrap.py', '.codex-plugin/plugin.json'):
        if not (root / relative).is_file():
            raise SetupError(f'The release bundle is missing {relative}.')
    declared = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8')).get('version')
    if declared != version:
        raise SetupError(f'Release v{version} declares product version {declared}.')


def extract(archive, target):
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    for path in (target / 'install.sh', *target.glob('scripts/*.sh')):
        path.chmod(0o755)


def install_release(archive, digest, version):
    target = RELEASES / version
    marker = target / '.sqlctx-bundle-sha256'
    if marker.is_file() and marker.read_text(encoding='ascii').strip() == digest:
        return target
    RELEASES.mkdir(parents=True, exist_ok=True)
    stage = RELEASES / f'.stage-{uuid.uuid4().hex}'
    try:
        extract(archive, stage)
        assert_release(stage, version)
        (stage / '.sqlctx-bundle-sha256').write_text(digest + '\n', encoding='ascii')
        if target.exists():
            previous = RELEASES / f'.old-{uuid.uuid4().hex}'
            target.rename(previous)
            shutil.rmtree(previous, ignore_errors=True)
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return target


def remove_unused_releases(current):
    # Install provenance records the release each host was installed from, so a release some
    # host still points at is kept.
    kept = {current.resolve()}
    for host in HOSTS:
        try:
            source = json.loads((destination(host) / '.sqlctx-install.json').read_text(encoding='utf-8')).get('source_root')
            if isinstance(source, str):
                kept.add(Path(source).resolve())
        except (OSError, ValueError, AttributeError):
            pass
    for path in RELEASES.iterdir():
        if path.is_dir() and path.resolve() not in kept:
            shutil.rmtree(path, ignore_errors=True)


MARKERS = (
    (r'^\[preflight\]', 'Checking Python', 0.05),
    (r'^\[package\] Installing', 'Installing the Python package', 0.15),
    (r'^\[plugin\]', 'Registering the plugin', 0.5),
    (r'^\[service\]', 'Installing the shared service', 0.65),
    (r'^\[verify\]', 'Checking the installation', 0.9),
)


def read_line(line):
    """Map the release installer's own stage markers onto the display."""
    text = line.strip()
    if not text:
        return None
    for pattern, stage, share in MARKERS:
        if re.match(pattern, text):
            return {'stage': stage, 'share': share, 'detail': re.sub(r'^\[\w+\]\s*', '', text)}
    return {'detail': text}


def run(command, start, end, label, cwd, env):
    step(start, label)
    log('### ' + label)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               text=True, encoding='utf-8', errors='replace', cwd=cwd, env=env)
    output = []
    try:
        for line in process.stdout:
            line = line.rstrip('\n')
            output.append(line)
            log(line)
            parsed = read_line(line)
            if parsed is None:
                continue
            if 'stage' in parsed:
                step(start + int((end - start) * parsed['share']), parsed['stage'])
            ui.note(parsed['detail'])
        code = process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
        process.stdout.close()
    if code:
        raise SetupError(f'{label} failed (exit {code}).')
    return output


def install_host(item, release, python, start, end, cwd, env):
    if item['mode'] == 'native':
        # Package and service only, through the release's own runtime bootstrap.
        verb = 'update' if item['update'] else 'install'
        command = [python, '-B', str(release / 'scripts/bootstrap.py'), '--operation', verb, '--source-root', str(release)]
    else:
        command = ['bash', str(release / 'install.sh'), '--harness', item['host']]
        if item['update']:
            command.append('--update')
    run(command, start, end, f"Installing for {item['host']}", cwd, env)


def check_host(item, python, version, cwd, env, percent):
    label = f"Checking {item['host']} versions"
    step(percent, label)
    log('### ' + label)
    result = subprocess.run([python, '-B', '-m', 'sqlctx.cli', 'doctor', 'check', '--host', item['host']],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=cwd, env=env,
                            stdin=subprocess.DEVNULL, check=False)
    for line in (result.stdout + result.stderr).splitlines():
        log(line)
    lines = [line for line in result.stdout.splitlines() if line.startswith('{')]
    if not lines:
        raise SetupError(f"sqlctx doctor check --host {item['host']} returned no report (exit {result.returncode}).")
    report = json.loads(lines[-1])
    versions = report.get('versions', {})
    outcome = {'host': item['host'], 'plugin': versions.get('plugin'), 'problems': [], 'notes': [],
               'profiles': (report.get('profiles') or {}).get('configured')}
    for surface in ('package', 'service'):
        if versions.get(surface) != version:
            outcome['problems'].append(f'{surface} is {versions.get(surface)}, expected {version}')
    plugin = versions.get('plugin')
    if plugin is None:
        staged = f'; staged plugin files are {version}' if versions.get('staged_plugin') == version else ''
        outcome['notes'].append(f"{item['host']}: the host CLI did not report the plugin version{staged}. "
                                f"Check with: sqlctx doctor check --host {item['host']}")
    elif plugin != version:
        if item['mode'] == 'native':
            outcome['notes'].append(f"{item['host']}: plugin {plugin} comes from {item['registration']}. "
                                    f'Refresh it with: {refresh_command(item)}')
        else:
            outcome['problems'].append(f"{item['host']} plugin is {plugin}, expected {version}")
    for finding in report.get('findings', []):
        code = finding.get('code') if isinstance(finding, dict) else None
        if code and code not in ('VERSION_DRIFT', 'PLUGIN_VERSION_UNVERIFIED'):
            outcome['notes'].append(f"{item['host']}: doctor reports {code}")
    if result.returncode:
        outcome['notes'].append(f"{item['host']}: sqlctx doctor check exited {result.returncode}; run it to see why.")
    return outcome


def externally_managed():
    marker = Path(sysconfig.get_path('stdlib')) / 'EXTERNALLY-MANAGED'
    return marker.is_file() and not os.environ.get('PIP_BREAK_SYSTEM_PACKAGES')


def setup(temp):
    step(2, 'Checking Python')
    python = sys.executable
    if externally_managed():
        raise SetupError(f'{python} is managed by the operating system (PEP 668), so pip cannot install into it. '
                         'Put a separately installed Python 3.11+ first on PATH (python.org, Homebrew or pyenv), then run this again.')
    step(5, 'Selecting hosts')
    plans = [plan(host) for host in select_hosts()]
    step(8, 'Finding the latest release')
    tag = latest_tag()
    version = tag[1:]
    base = f'{REPOSITORY_URL}/releases/download/{tag}/'
    archive = temp / BUNDLE
    download(base + BUNDLE, archive, 67108864, f'{BUNDLE} ({tag})')
    step(40, 'Downloading the checksum')
    download(base + BUNDLE + '.sha256', temp / (BUNDLE + '.sha256'), 4096, BUNDLE + '.sha256')
    step(44, 'Verifying SHA-256')
    match = re.fullmatch(r'([a-f0-9]{64})  sql-context-pack-install\.zip\s*',
                         (temp / (BUNDLE + '.sha256')).read_text(encoding='ascii', errors='replace'))
    if not match:
        raise SetupError('The release checksum file is invalid.')
    digest = match[1]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise SetupError('Release checksum mismatch. Existing installation was not changed.')
    step(48, 'Checking bundle contents')
    check_bundle(archive)

    if DRY_RUN:
        step(50, 'Unpacking for inspection')
        release = temp / 'release'
        extract(archive, release)
        assert_release(release, version)
        ui.close()
        print(f'Dry run: SQL Context Pack {version} ({tag}) downloaded and verified. Nothing was installed.')
        print(f'Python: {python}')
        print(f'Release folder: {RELEASES / version}')
        for item in plans:
            action = 'update' if item['update'] else 'install'
            if item['mode'] == 'native':
                what = f"package and service only; the plugin stays {item['registration']} from its own marketplace"
            elif item['host'] == 'agy':
                what = f'{action} package, service, skill files and MCP entry'
            elif item['registration']:
                what = f"{action} package, service and plugin {item['registration']}"
            else:
                what = f'{action} package, service and extension'
            print(f"  {item['host']}: {what}")
        return

    step(50, f'Unpacking {version}')
    release = install_release(archive, digest, version)
    LOGS.mkdir(parents=True, exist_ok=True)
    state['log_path'] = LOGS / f'{OPERATION}.log'
    state['log'] = state['log_path'].open('w', encoding='utf-8')
    log(f'sql-context-pack {OPERATION} {version} from {tag}; python {python}')
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
           'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PIP_NO_INPUT': '1'}

    band = 38 // len(plans)
    for index, item in enumerate(plans):
        # Children start in the private temp folder, never the folder the owner ran this from.
        install_host(item, release, python, 55 + index * band, 55 + (index + 1) * band, temp, env)
    results = [check_host(item, python, version, temp, env, 94 + index * 5 // len(plans))
               for index, item in enumerate(plans)]
    problems = [problem for result in results for problem in result['problems']]
    if problems:
        raise SetupError('Verification failed: ' + '; '.join(problems))

    step(99, 'Finishing')
    remove_unused_releases(release)
    step(100, 'Ready')
    ui.close()
    print(f'SQL Context Pack {version} is ready.')
    print(f'  package {version}, service {version} (127.0.0.1)')
    for result in results:
        print(f"  {result['host']}: plugin {result['plugin'] or 'not reported'}")
    for result in results:
        for note in result['notes']:
            print(f'  ! {note}')
    scripts = Path(sysconfig.get_path('scripts', sysconfig.get_preferred_scheme('user')))
    if str(scripts) not in os.environ.get('PATH', '').split(os.pathsep):
        print(f'  ! {scripts} is not on PATH. Add this line to your shell profile: export PATH="{scripts}:$PATH"')
    print('Next:')
    print('  1. Open a new session in your host; Skill instructions load when a session starts.')
    if any(result['profiles'] == 0 for result in results):
        print('  2. Create a database profile in this terminal: sqlctx profile configure')
    for result in results:
        if result['host'] == 'codex':
            print('     Codex: $sql-context-pack profiles')
        elif result['host'] == 'claude':
            print('     Claude Code: /sql-context-pack:sql-context-pack profiles')
        else:
            print(f"     {result['host']}: ask 'Use the sql-context-pack skill to list profiles.'")
    print(f"Log: {state['log_path']}")


def fail(message):
    ui.close()
    print(f"SQL Context Pack {OPERATION} failed while: {cells(state['stage'], 160)}", file=sys.stderr)
    print(cells(message, 400), file=sys.stderr)
    if state['tail']:
        print('Last installer output:', file=sys.stderr)
        for line in state['tail']:
            print('  ' + cells(line, 200), file=sys.stderr)
    if state['log_path']:
        print(f"Full log: {state['log_path']}", file=sys.stderr)


try:
    with tempfile.TemporaryDirectory(prefix='sqlctx-setup-') as temporary:
        setup(Path(temporary))
except KeyboardInterrupt:
    fail('Cancelled.')
    raise SystemExit(130) from None
except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
    fail(str(error))
    raise SystemExit(1) from None
finally:
    ui.close()
    if state['log'] is not None:
        state['log'].close()
PY
