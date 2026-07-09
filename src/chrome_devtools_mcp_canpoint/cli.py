from __future__ import annotations

import argparse
import json
import atexit
import ctypes
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tempfile
import uuid
import urllib.error
import urllib.request
from importlib import resources
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_CHROME_PATHS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)
DEFAULT_SESSION_ROOT_NAME = ".chrome-mcp-sessions"
PROFILE_MODES = ("isolated", "inherit", "copy")
WINDOW_MODES = ("quiet", "visible", "headless")
LAUNCH_MODES = ("lazy", "eager")
MCP_LAZY_TRIGGER_METHODS = frozenset({"tools/call", "resources/read", "prompts/get"})
CHROME_DEVTOOLS_MCP_PACKAGE_NAME = "chrome-devtools-mcp"
TOOLS_LIST_METADATA_TIMEOUT_SECONDS = 5.0
MCP_PROTOCOL_VERSION = "2025-06-18"
PACKAGE_VERSION = "0.1.10"
DEBUG_LOG_ENV = "CHROME_DEVTOOLS_MCP_CANPOINT_LOG"
TOOLS_LIST_FALLBACK_RESOURCE = "_tools_list_fallback.json"
MCP_EMPTY_LIST_RESULTS = {
    "resources/list": {"resources": []},
    "resources/templates/list": {"resourceTemplates": []},
    "prompts/list": {"prompts": []},
}
DEFAULT_SOURCE_PROFILE = "Default"
ALWAYS_EXCLUDED_PROFILE_NAMES = frozenset(
    {
        "BrowserMetrics",
        "Crashpad",
        "CrashpadMetrics-active.pma",
        "GrShaderCache",
        "GPUCache",
        "ShaderCache",
        "Safe Browsing",
        "segmentation_platform",
        "SingletonCookie",
        "SingletonLock",
        "SingletonSocket",
        "lockfile",
    }
)
SENSITIVE_PROFILE_NAMES = frozenset(
    {
        "Cookies",
        "Login Data",
        "Login Data For Account",
        "Network",
        "Sessions",
        "Web Data",
    }
)


@dataclass(frozen=True)
class ChromeSessionConfig:
    chrome_path: Path
    port: int
    user_data_dir: Path
    profile_directory: str | None
    headless: bool
    extra_args: tuple[str, ...]
    window_mode: str = "visible"


@dataclass(frozen=True)
class ProfileSelection:
    user_data_dir: Path
    generated_session_dir: bool
    profile_directory: str | None


@dataclass(frozen=True)
class ProfilePlan:
    user_data_dir: Path
    generated_session_dir: bool
    profile_directory: str | None
    profile_mode: str
    source_user_data_dir: Path | None
    source_profile: str
    include_sensitive_profile_data: bool


@dataclass(frozen=True)
class ChromeDevToolsMcpMetadataCommand:
    npx_command: str
    package_spec: str
    server_args: tuple[str, ...]


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def devtools_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def build_chrome_args(config: ChromeSessionConfig) -> list[str]:
    args = [
        str(config.chrome_path),
        f"--remote-debugging-port={config.port}",
        f"--user-data-dir={config.user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
    ]
    if config.profile_directory:
        args.append(f"--profile-directory={config.profile_directory}")
    if config.headless or config.window_mode == "headless":
        args.append("--headless=new")
    elif config.window_mode == "quiet":
        args.append("--start-minimized")
    args.extend(config.extra_args)
    return args


def build_chrome_startupinfo(window_mode: str):
    if os.name != "nt" or window_mode != "quiet":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 7
    return startupinfo


def downstream_env(base_env: Mapping[str, str], port: int, user_data_dir: Path) -> dict[str, str]:
    env = dict(base_env)
    url = devtools_url(port)
    env.update(
        {
            "CHROME_DEVTOOLS_URL": url,
            "CHROME_REMOTE_DEBUGGING_PORT": str(port),
            "CHROME_USER_DATA_DIR": str(user_data_dir),
            "BROWSER_URL": url,
        }
    )
    return env


def expand_downstream_command(
    command: Sequence[str], port: int, user_data_dir: Path
) -> list[str]:
    replacements = {
        "browser_url": devtools_url(port),
        "devtools_url": devtools_url(port),
        "port": str(port),
        "user_data_dir": str(user_data_dir),
    }
    return [arg.format(**replacements) for arg in command]


def is_npx_executable(value: str) -> bool:
    return Path(value).name.lower() in {"npx", "npx.cmd", "npx.exe", "npx.ps1"}


def is_chrome_devtools_mcp_package_spec(value: str) -> bool:
    return value == CHROME_DEVTOOLS_MCP_PACKAGE_NAME or value.startswith(
        f"{CHROME_DEVTOOLS_MCP_PACKAGE_NAME}@"
    )


def resolve_windows_command_shim(executable: str) -> str:
    if os.name != "nt":
        return executable
    if any(separator in executable for separator in ("/", "\\")) or Path(executable).suffix:
        return executable

    pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
    preferred_extensions = (".exe", ".cmd", ".bat", ".com")
    extensions = sorted(
        {extension.lower() for extension in pathext if extension},
        key=lambda extension: preferred_extensions.index(extension)
        if extension in preferred_extensions
        else len(preferred_extensions),
    )
    for extension in extensions:
        candidate = shutil.which(f"{executable}{extension}")
        if candidate:
            return candidate

    return executable


def find_chrome_devtools_mcp_metadata_command(
    command: Sequence[str],
) -> ChromeDevToolsMcpMetadataCommand | None:
    for index, value in enumerate(command):
        if not is_npx_executable(value):
            continue
        for package_index in range(index + 1, len(command)):
            package_spec = command[package_index]
            if is_chrome_devtools_mcp_package_spec(package_spec):
                return ChromeDevToolsMcpMetadataCommand(
                    npx_command=resolve_windows_command_shim(value),
                    package_spec=package_spec,
                    server_args=tuple(command[package_index + 1 :]),
                )
    return None


def chrome_devtools_mcp_tools_list_metadata(
    metadata_command: ChromeDevToolsMcpMetadataCommand,
    env: Mapping[str, str],
    timeout_seconds: float = TOOLS_LIST_METADATA_TIMEOUT_SECONDS,
) -> dict:
    script = r"""
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const packageName = 'chrome-devtools-mcp';
const serverArgs = JSON.parse(process.argv[2] ?? '[]');
let packageRoot;

for (const entry of (process.env.PATH || '').split(path.delimiter)) {
  if (!entry) {
    continue;
  }
  const candidate = path.resolve(entry, '..', packageName);
  if (fs.existsSync(path.join(candidate, 'package.json'))) {
    packageRoot = candidate;
    break;
  }
}

if (!packageRoot) {
  throw new Error(`Could not locate ${packageName} from npx PATH.`);
}

const importFromRoot = async relativePath => {
  return import(pathToFileURL(path.join(packageRoot, relativePath)).href);
};

const { parseArguments } = await importFromRoot('build/src/bin/chrome-devtools-mcp-cli-options.js');
const { VERSION } = await importFromRoot('build/src/version.js');
const { createMcpServer } = await importFromRoot('build/src/index.js');

process.argv = ['node', 'chrome-devtools-mcp', ...serverArgs];
const args = parseArguments(VERSION);
const { server } = await createMcpServer(args, {});
const handler = server.server._requestHandlers.get('tools/list');
if (!handler) {
  throw new Error('chrome-devtools-mcp did not register a tools/list handler.');
}

const result = await handler({ method: 'tools/list', params: {} }, {});
console.log(JSON.stringify(result));
process.exit(0);
"""
    result = subprocess.run(
        [
            metadata_command.npx_command,
            "-y",
            "-p",
            metadata_command.package_spec,
            "node",
            "--input-type=module",
            "-",
            json.dumps(list(metadata_command.server_args)),
        ],
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"metadata probe exited {result.returncode}")
    return json.loads(result.stdout)


def fallback_tools_list_metadata() -> dict:
    resource = resources.files(__package__).joinpath(TOOLS_LIST_FALLBACK_RESOURCE)
    return json.loads(resource.read_text(encoding="utf-8"))


def make_tools_list_metadata_provider(command: Sequence[str], env: Mapping[str, str]):
    cached_result: dict | None = None

    def provider() -> dict:
        nonlocal cached_result
        if cached_result is None:
            cached_result = fallback_tools_list_metadata()
        return cached_result

    return provider


def encode_stdio_json(message: Mapping[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return payload + b"\n"


def read_stdio_message(source) -> tuple[bytes, bytes] | None:
    first_line = source.readline()
    if first_line == b"":
        return None

    name, separator, value = first_line.partition(b":")
    if separator and name.lower() == b"content-length":
        header_chunks = [first_line]
        content_length = int(value.strip())

        while True:
            line = source.readline()
            if line == b"":
                raise EOFError("Incomplete MCP stdio header.")
            header_chunks.append(line)
            if line in (b"\r\n", b"\n"):
                break
            name, separator, value = line.partition(b":")
            if separator and name.lower() == b"content-length":
                content_length = int(value.strip())

        payload = source.read(content_length)
        if len(payload) != content_length:
            raise EOFError("Incomplete MCP stdio payload.")
        return b"".join(header_chunks), payload

    stripped = first_line.strip()
    if not stripped:
        return read_stdio_message(source)
    return b"", stripped + b"\n"


def decode_json_rpc_payload(payload: bytes) -> dict | None:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(message, dict):
        return message
    return None


def json_rpc_startup_error(request_id: object, exc: Exception) -> bytes:
    return encode_stdio_json(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": f"Chrome startup failed: {exc}",
            },
        }
    )


def json_rpc_result_response(request_id: object, result: Mapping[str, object]) -> bytes:
    return encode_stdio_json({"jsonrpc": "2.0", "id": request_id, "result": dict(result)})


def json_rpc_initialize_response(request_id: object) -> bytes:
    return json_rpc_result_response(
        request_id,
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": "chrome-devtools-mcp-canpoint",
                "version": PACKAGE_VERSION,
            },
        },
    )


def resolve_session_root(value: str | None) -> Path:
    if value:
        return Path(value).resolve()
    return (Path.cwd() / DEFAULT_SESSION_ROOT_NAME).resolve()


def default_chrome_user_data_dir() -> Path:
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Google" / "Chrome" / "User Data"
        return Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def resolve_source_user_data_dir_path(value: str | None) -> Path:
    source_user_data_dir = Path(value).expanduser() if value else default_chrome_user_data_dir()
    return source_user_data_dir.resolve()


def resolve_source_user_data_dir(value: str | None) -> Path:
    source_user_data_dir = resolve_source_user_data_dir_path(value)
    if not source_user_data_dir.exists():
        raise FileNotFoundError(
            f"Chrome user data directory not found: {source_user_data_dir}"
        )
    if not source_user_data_dir.is_dir():
        raise NotADirectoryError(
            f"Chrome user data path is not a directory: {source_user_data_dir}"
        )
    return source_user_data_dir


def resolve_source_profile_dir(source_user_data_dir: Path, profile_name: str) -> Path:
    source_profile_dir = (source_user_data_dir / profile_name).resolve()
    if not source_profile_dir.exists():
        raise FileNotFoundError(f"Chrome profile not found: {source_profile_dir}")
    if not source_profile_dir.is_dir():
        raise NotADirectoryError(f"Chrome profile path is not a directory: {source_profile_dir}")
    return source_profile_dir


def should_copy_profile_path(path: Path, include_sensitive: bool) -> bool:
    if any(part in ALWAYS_EXCLUDED_PROFILE_NAMES for part in path.parts):
        return False
    if not include_sensitive and any(part in SENSITIVE_PROFILE_NAMES for part in path.parts):
        return False
    return True


def profile_copy_ignore(include_sensitive: bool):
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        base = Path(directory)
        for name in names:
            if not should_copy_profile_path(base / name, include_sensitive):
                ignored.add(name)
        return ignored

    return ignore


def copy_chrome_profile(
    source_user_data_dir: Path,
    target_user_data_dir: Path,
    source_profile: str,
    include_sensitive: bool,
) -> None:
    source_profile_dir = resolve_source_profile_dir(source_user_data_dir, source_profile)
    target_profile_dir = target_user_data_dir / source_profile
    target_user_data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_profile_dir,
        target_profile_dir,
        ignore=profile_copy_ignore(include_sensitive),
        dirs_exist_ok=True,
    )


def copy_chrome_local_state(
    source_user_data_dir: Path, target_user_data_dir: Path, include_sensitive: bool
) -> None:
    local_state = source_user_data_dir / "Local State"
    if include_sensitive and local_state.exists() and local_state.is_file():
        target_user_data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_state, target_user_data_dir / "Local State")


def plan_user_data_dir(
    user_data_dir: str | None,
    profile_mode: str,
    session_root: str | None,
    source_user_data_dir: str | None,
    source_profile: str,
    include_sensitive_profile_data: bool,
) -> ProfilePlan:
    if user_data_dir:
        return ProfilePlan(
            Path(user_data_dir).expanduser().resolve(),
            False,
            None,
            "explicit",
            None,
            source_profile,
            include_sensitive_profile_data,
        )

    if profile_mode == "isolated":
        return ProfilePlan(
            resolve_session_root(session_root) / uuid.uuid4().hex,
            True,
            None,
            "isolated",
            None,
            source_profile,
            include_sensitive_profile_data,
        )

    if profile_mode in {"inherit", "copy"}:
        source_root = resolve_source_user_data_dir_path(source_user_data_dir)
        if profile_mode == "inherit":
            return ProfilePlan(
                source_root,
                False,
                source_profile,
                "inherit",
                source_root,
                source_profile,
                include_sensitive_profile_data,
            )
        return ProfilePlan(
            resolve_session_root(session_root) / uuid.uuid4().hex,
            True,
            source_profile,
            "copy",
            source_root,
            source_profile,
            include_sensitive_profile_data,
        )

    raise ValueError(f"Unknown profile mode: {profile_mode}")


def materialize_profile(plan: ProfilePlan) -> ProfileSelection:
    if plan.profile_mode in {"explicit", "isolated"}:
        plan.user_data_dir.mkdir(parents=True, exist_ok=True)
        return ProfileSelection(
            plan.user_data_dir,
            plan.generated_session_dir,
            plan.profile_directory,
        )

    if plan.profile_mode == "inherit":
        source_root = resolve_source_user_data_dir(str(plan.source_user_data_dir))
        resolve_source_profile_dir(source_root, plan.source_profile)
        return ProfileSelection(source_root, False, plan.source_profile)

    if plan.profile_mode == "copy":
        source_root = resolve_source_user_data_dir(str(plan.source_user_data_dir))
        copy_chrome_profile(
            source_root,
            plan.user_data_dir,
            plan.source_profile,
            plan.include_sensitive_profile_data,
        )
        copy_chrome_local_state(
            source_root,
            plan.user_data_dir,
            plan.include_sensitive_profile_data,
        )
        return ProfileSelection(plan.user_data_dir, True, plan.source_profile)

    raise ValueError(f"Unknown profile mode: {plan.profile_mode}")


def select_user_data_dir(
    user_data_dir: str | None,
    profile_mode: str,
    session_root: str | None,
    source_user_data_dir: str | None,
    source_profile: str,
    include_sensitive_profile_data: bool,
) -> ProfileSelection:
    return materialize_profile(
        plan_user_data_dir(
            user_data_dir,
            profile_mode,
            session_root,
            source_user_data_dir,
            source_profile,
            include_sensitive_profile_data,
        )
    )


def resolve_chrome_path(value: str | None) -> Path:
    candidates: list[Path] = []
    if value:
        candidates.append(Path(value))
    if os.environ.get("CHROME_PATH"):
        candidates.append(Path(os.environ["CHROME_PATH"]))
    candidates.extend(DEFAULT_CHROME_PATHS)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Chrome executable not found. Pass --chrome-path or set CHROME_PATH."
    )


def wait_for_devtools(port: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    endpoint = f"{devtools_url(port)}/json/version"
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(0.1)

    message = f"Timed out waiting for Chrome DevTools at {endpoint}"
    if last_error:
        message = f"{message}: {last_error}"
    raise TimeoutError(message)


def devtools_endpoint_ready(port: int, timeout_seconds: float = 1.0) -> bool:
    endpoint = f"{devtools_url(port)}/json/version"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.1)
    return False


def bridge_stream(source, target) -> None:
    source_fd = source.fileno()
    target_fd = target.fileno()
    try:
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            os.write(target_fd, chunk)
    finally:
        try:
            target.close()
        except OSError:
            pass


class DebugLogger:
    def __init__(self, path: Path | None = None):
        if path is None:
            path_value = os.environ.get(DEBUG_LOG_ENV)
            path = Path(path_value) if path_value else Path(tempfile.gettempdir()) / "chrome-devtools-mcp-canpoint.log"
        self.path = path
        self._lock = threading.Lock()

    def write(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as log:
                log.write(f"{timestamp} {message.rstrip()}\n")
        except OSError:
            pass

    def flush(self) -> None:
        return


def bridge_mcp_server_stream(source, target, suppressed_response_ids, suppressed_lock) -> None:
    try:
        while True:
            message = read_stdio_message(source)
            if message is None:
                break
            headers, payload = message
            json_rpc_message = decode_json_rpc_payload(payload)
            response_id = json_rpc_message.get("id") if json_rpc_message is not None else None
            if (
                response_id is not None
                and json_rpc_message is not None
                and "method" not in json_rpc_message
            ):
                with suppressed_lock:
                    if response_id in suppressed_response_ids:
                        suppressed_response_ids.remove(response_id)
                        continue
            target.write(headers)
            target.write(payload)
            target.flush()
    finally:
        try:
            target.close()
        except OSError:
            pass


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsProcessJob:
    def __init__(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            self._handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def add_process(self, process: subprocess.Popen[bytes]) -> None:
        ok = self._kernel32.AssignProcessToJobObject(self._handle, int(process._handle))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = 0


def create_process_job():
    if os.name != "nt":
        return None
    try:
        return WindowsProcessJob()
    except OSError:
        return None


def add_process_to_job(process_job, process: subprocess.Popen[bytes]) -> None:
    if process_job is None:
        return
    try:
        process_job.add_process(process)
    except OSError:
        process_job.close()


def start_chrome_process(config: ChromeSessionConfig) -> tuple[subprocess.Popen[bytes], object | None]:
    process_job = create_process_job()
    try:
        chrome = subprocess.Popen(
            build_chrome_args(config),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=build_chrome_startupinfo(config.window_mode),
        )
    except Exception:
        if process_job is not None:
            process_job.close()
        raise
    add_process_to_job(process_job, chrome)
    return chrome, process_job


class ChromeSessionManager:
    def __init__(
        self,
        chrome_path_value: str | None,
        port: int,
        profile_plan: ProfilePlan,
        keep_profile: bool,
        devtools_timeout: float,
        headless: bool,
        window_mode: str,
        extra_args: Sequence[str],
    ) -> None:
        self._chrome_path_value = chrome_path_value
        self._port = port
        self._profile_plan = profile_plan
        self._keep_profile = keep_profile
        self._devtools_timeout = devtools_timeout
        self._headless = headless
        self._window_mode = "headless" if headless else window_mode
        self._extra_args = tuple(extra_args)
        self._lock = threading.Lock()
        self._chrome: subprocess.Popen[bytes] | None = None
        self._chrome_process_job = None
        self._started = False
        self._profile_materialized = False

    def ensure_started(self) -> None:
        with self._lock:
            if self._started:
                if self._chrome is not None and self._chrome.poll() is None:
                    return
                # The launcher process we spawned may have exited because
                # Chrome relaunched itself (notably de-elevation when this
                # wrapper runs elevated). The relaunched browser process keeps
                # serving the DevTools endpoint under a different pid, so treat
                # the session as alive as long as the endpoint still responds.
                if devtools_endpoint_ready(self._port):
                    return
                raise RuntimeError("Chrome exited before the MCP request could be handled.")

            profile_selection = materialize_profile(self._profile_plan)
            self._profile_materialized = True
            chrome_path = resolve_chrome_path(self._chrome_path_value)
            config = ChromeSessionConfig(
                chrome_path=chrome_path,
                port=self._port,
                user_data_dir=profile_selection.user_data_dir,
                profile_directory=profile_selection.profile_directory,
                headless=self._headless,
                extra_args=self._extra_args,
                window_mode=self._window_mode,
            )
            chrome, chrome_process_job = start_chrome_process(config)
            self._chrome = chrome
            self._chrome_process_job = chrome_process_job
            try:
                wait_for_devtools(self._port, self._devtools_timeout)
            except Exception:
                terminate_process(chrome)
                if chrome_process_job is not None:
                    chrome_process_job.close()
                self._chrome = None
                self._chrome_process_job = None
                if profile_selection.generated_session_dir and not self._keep_profile:
                    remove_directory_with_retries(profile_selection.user_data_dir)
                self._profile_materialized = False
                raise
            self._started = True

    def cleanup(self) -> None:
        with self._lock:
            chrome = self._chrome
            chrome_process_job = self._chrome_process_job
            profile_materialized = self._profile_materialized
            self._chrome = None
            self._chrome_process_job = None
            self._started = False
            self._profile_materialized = False

        if chrome is not None:
            terminate_process(chrome)
            if chrome_process_job is not None:
                chrome_process_job.close()
            terminate_chrome_profile_processes(self._profile_plan.user_data_dir)
        if profile_materialized and self._profile_plan.generated_session_dir and not self._keep_profile:
            remove_directory_with_retries(self._profile_plan.user_data_dir)


def terminate_process(process: subprocess.Popen[bytes], timeout_seconds: float = 5) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout_seconds)
        return
    process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)


def terminate_chrome_profile_processes(user_data_dir: Path) -> None:
    if os.name != "nt":
        return
    powershell_path = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not powershell_path.exists():
        return
    profile_marker = str(user_data_dir)
    command = (
        "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like ('*' + $env:CHROME_MCP_PROFILE_MARKER + '*') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    env = dict(os.environ)
    env["CHROME_MCP_PROFILE_MARKER"] = profile_marker
    subprocess.run(
        [str(powershell_path), "-NoProfile", "-Command", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        check=False,
    )


def remove_directory_with_retries(path: Path, attempts: int = 20, delay_seconds: float = 0.25) -> None:
    for attempt in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        if attempt < attempts - 1:
            time.sleep(delay_seconds)


def bridge_mcp_client_stream(
    source,
    target,
    ensure_chrome,
    error_target,
    log_target,
    tools_list_metadata_provider=None,
    suppressed_response_ids=None,
    suppressed_lock=None,
) -> None:
    try:
        initialize_message: tuple[bytes, bytes] | None = None
        initialized_message: tuple[bytes, bytes] | None = None
        downstream_initialized = False

        while True:
            message = read_stdio_message(source)
            if message is None:
                break
            headers, payload = message
            json_rpc_message = decode_json_rpc_payload(payload)
            method = json_rpc_message.get("method") if json_rpc_message is not None else None
            print(f"client method: {method}", file=log_target)

            if method == "initialize" and json_rpc_message is not None and "id" in json_rpc_message:
                initialize_message = (headers, payload)
                if suppressed_response_ids is not None and suppressed_lock is not None:
                    with suppressed_lock:
                        suppressed_response_ids.add(json_rpc_message.get("id"))
                error_target.write(json_rpc_initialize_response(json_rpc_message.get("id")))
                error_target.flush()
                print("answered initialize locally", file=log_target)
                continue

            if method == "notifications/initialized" and json_rpc_message is not None:
                initialized_message = (headers, payload)
                print("stored initialized notification", file=log_target)
                continue

            if method in MCP_EMPTY_LIST_RESULTS and json_rpc_message is not None:
                if "id" in json_rpc_message:
                    error_target.write(
                        json_rpc_result_response(
                            json_rpc_message.get("id"),
                            MCP_EMPTY_LIST_RESULTS[method],
                        )
                    )
                    error_target.flush()
                    print(f"answered {method} locally", file=log_target)
                continue

            if method == "tools/list" and json_rpc_message is not None:
                if tools_list_metadata_provider is not None and "id" in json_rpc_message:
                    try:
                        metadata = tools_list_metadata_provider()
                    except Exception as exc:
                        print(
                            f"Failed to synthesize chrome-devtools-mcp tools/list metadata: {exc}",
                            file=log_target,
                        )
                    else:
                        error_target.write(
                            json_rpc_result_response(json_rpc_message.get("id"), metadata)
                        )
                        error_target.flush()
                        print("answered tools/list from synthesized metadata", file=log_target)
                        continue

                try:
                    ensure_chrome()
                except Exception as exc:
                    if "id" in json_rpc_message:
                        error_target.write(json_rpc_startup_error(json_rpc_message.get("id"), exc))
                        error_target.flush()
                    else:
                        print(f"Chrome startup failed: {exc}", file=log_target)
                    continue
                if not downstream_initialized:
                    print("replaying startup messages to downstream", file=log_target)
                    if initialize_message is not None:
                        target.write(initialize_message[0])
                        target.write(initialize_message[1])
                    if initialized_message is not None:
                        target.write(initialized_message[0])
                        target.write(initialized_message[1])
                    target.flush()
                    downstream_initialized = True

            if method in MCP_LAZY_TRIGGER_METHODS:
                try:
                    ensure_chrome()
                except Exception as exc:
                    if "id" in json_rpc_message:
                        error_target.write(json_rpc_startup_error(json_rpc_message.get("id"), exc))
                        error_target.flush()
                    else:
                        print(f"Chrome startup failed: {exc}", file=log_target)
                    continue
                if not downstream_initialized:
                    print("replaying startup messages to downstream", file=log_target)
                    if initialize_message is not None:
                        target.write(initialize_message[0])
                        target.write(initialize_message[1])
                    if initialized_message is not None:
                        target.write(initialized_message[0])
                        target.write(initialized_message[1])
                    target.flush()
                    downstream_initialized = True

            target.write(headers)
            target.write(payload)
            target.flush()
            print(f"forwarded method downstream: {method}", file=log_target)
    finally:
        try:
            target.close()
        except OSError:
            pass


def resolve_downstream_command(command: Sequence[str]) -> list[str]:
    resolved = list(command)
    if not resolved:
        return resolved

    resolved[0] = resolve_windows_command_shim(resolved[0])
    return resolved


class LazyDownstreamTarget:
    def __init__(
        self,
        command: Sequence[str],
        env: Mapping[str, str],
        stdout_target,
        suppressed_response_ids,
        suppressed_lock,
        log_target,
    ):
        self.command = list(command)
        self.env = dict(env)
        self.stdout_target = stdout_target
        self.suppressed_response_ids = suppressed_response_ids
        self.suppressed_lock = suppressed_lock
        self.log_target = log_target
        self.process: subprocess.Popen[bytes] | None = None
        self.stdout_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def ensure_started(self) -> subprocess.Popen[bytes]:
        with self._lock:
            if self.process is not None:
                return self.process
            print(f"starting downstream: {self.command!r}", file=self.log_target)
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self.env,
            )
            if self.process.stdout is None:
                raise RuntimeError("Downstream stdout pipe was not created.")
            self.stdout_thread = threading.Thread(
                target=bridge_mcp_server_stream,
                args=(
                    self.process.stdout,
                    self.stdout_target,
                    self.suppressed_response_ids,
                    self.suppressed_lock,
                ),
                daemon=True,
            )
            self.stdout_thread.start()
            print(f"downstream started pid={self.process.pid}", file=self.log_target)
            return self.process

    def write(self, data: bytes) -> int:
        process = self.ensure_started()
        if process.stdin is None:
            raise RuntimeError("Downstream stdin pipe was not created.")
        return process.stdin.write(data)

    def flush(self) -> None:
        process = self.ensure_started()
        if process.stdin is None:
            raise RuntimeError("Downstream stdin pipe was not created.")
        process.stdin.flush()

    def close(self) -> None:
        if self.process is None or self.process.stdin is None:
            return
        try:
            self.process.stdin.close()
        except OSError:
            pass

    def wait(self) -> int:
        if self.process is None:
            return 0
        return self.process.wait()

    def poll(self) -> int | None:
        if self.process is None:
            return 0
        return self.process.poll()

    def terminate(self) -> None:
        if self.process is not None and self.process.poll() is None:
            terminate_process(self.process)

    def join_stdout(self, timeout: float | None = None) -> None:
        if self.stdout_thread is not None:
            self.stdout_thread.join(timeout=timeout)


def run_downstream(command: Sequence[str], env: Mapping[str, str], ensure_chrome=None) -> int:
    resolved_command = resolve_downstream_command(command)
    log_target = DebugLogger()
    print(f"run_downstream ensure_chrome={ensure_chrome is not None}", file=log_target)

    if ensure_chrome is not None:
        suppressed_response_ids = set()
        suppressed_lock = threading.Lock()
        downstream_target = LazyDownstreamTarget(
            resolved_command,
            env,
            sys.stdout.buffer,
            suppressed_response_ids,
            suppressed_lock,
            log_target,
        )
        stdin_thread = threading.Thread(
            target=bridge_mcp_client_stream,
            args=(
                sys.stdin.buffer,
                downstream_target,
                ensure_chrome,
                sys.stdout.buffer,
                log_target,
                make_tools_list_metadata_provider(resolved_command, env),
                suppressed_response_ids,
                suppressed_lock,
            ),
            daemon=True,
        )
        stdin_thread.start()
        try:
            stdin_thread.join()
            return downstream_target.wait()
        except KeyboardInterrupt:
            downstream_target.terminate()
            return 130
        finally:
            downstream_target.terminate()
            downstream_target.join_stdout(timeout=1)

    downstream = subprocess.Popen(
        resolved_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        env=dict(env),
    )

    suppressed_response_ids = set()
    suppressed_lock = threading.Lock()

    if ensure_chrome is None:
        stdin_thread = threading.Thread(
            target=bridge_stream,
            args=(sys.stdin.buffer, downstream.stdin),
            daemon=True,
        )
    else:
        stdin_thread = threading.Thread(
            target=bridge_mcp_client_stream,
            args=(
                sys.stdin.buffer,
                downstream.stdin,
                ensure_chrome,
                sys.stdout.buffer,
                sys.stderr,
                make_tools_list_metadata_provider(resolved_command, env),
                suppressed_response_ids,
                suppressed_lock,
            ),
            daemon=True,
        )
    if ensure_chrome is None:
        stdout_thread = threading.Thread(
            target=bridge_stream,
            args=(downstream.stdout, sys.stdout.buffer),
            daemon=True,
        )
    else:
        stdout_thread = threading.Thread(
            target=bridge_mcp_server_stream,
            args=(downstream.stdout, sys.stdout.buffer, suppressed_response_ids, suppressed_lock),
            daemon=True,
        )

    def stop_downstream_when_input_closes() -> None:
        stdin_thread.join()
        if downstream.poll() is None:
            time.sleep(0.5)
        if downstream.poll() is None:
            terminate_process(downstream)

    stdin_thread.start()
    stdout_thread.start()
    input_watcher = threading.Thread(target=stop_downstream_when_input_closes, daemon=True)
    input_watcher.start()

    try:
        return downstream.wait()
    except KeyboardInterrupt:
        terminate_process(downstream)
        return 130
    finally:
        stdout_thread.join(timeout=1)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a downstream Chrome DevTools MCP command with a session-local Chrome endpoint."
    )
    parser.add_argument(
        "--chrome-path",
        help="Path to chrome.exe. Defaults to CHROME_PATH or common Windows install paths.",
    )
    parser.add_argument(
        "--user-data-dir",
        help="Exact profile directory to use. Overrides --profile-mode and --session-root.",
    )
    parser.add_argument(
        "--profile-mode",
        choices=PROFILE_MODES,
        default="isolated",
        help="Chrome profile mode: isolated (default), inherit, or copy.",
    )
    parser.add_argument(
        "--source-user-data-dir",
        help="Chrome user data directory to inherit or copy. Defaults to the detected local Chrome profile root.",
    )
    parser.add_argument(
        "--source-profile",
        default=DEFAULT_SOURCE_PROFILE,
        help="Chrome profile directory name to copy or validate. Defaults to Default.",
    )
    parser.add_argument(
        "--include-sensitive-profile-data",
        action="store_true",
        help="Copy cookies, sessions, and saved-login databases in copy mode. Ignored unless --profile-mode copy is used.",
    )
    parser.add_argument(
        "--session-root",
        default=None,
        help="Directory for generated session profiles. Defaults to .chrome-mcp-sessions in the current working directory.",
    )
    parser.add_argument(
        "--keep-profile",
        action="store_true",
        help="Do not delete the session profile after the downstream command exits.",
    )
    parser.add_argument(
        "--launch-mode",
        choices=LAUNCH_MODES,
        default="lazy",
        help="When to start Chrome: lazy (default) starts on first browser-backed MCP request; eager preserves startup-time launch behavior.",
    )
    parser.add_argument(
        "--window-mode",
        choices=WINDOW_MODES,
        default="quiet",
        help="Chrome window mode: quiet (default), visible, or headless.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Backward-compatible alias for --window-mode headless.",
    )
    parser.add_argument(
        "--devtools-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for Chrome DevTools to become ready.",
    )
    parser.add_argument(
        "--chrome-arg",
        action="append",
        default=[],
        help="Extra argument passed to Chrome. Repeat for multiple values.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Downstream MCP command. Prefix it with --, for example: -- npx chrome-devtools-mcp",
    )
    return parser


def normalize_command(command: Sequence[str]) -> list[str]:
    normalized = list(command)
    if normalized and normalized[0] == "--":
        normalized = normalized[1:]
    if not normalized:
        raise ValueError("Missing downstream MCP command after --.")
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    try:
        command = normalize_command(args.command)
        port = find_free_port()
        profile_plan = plan_user_data_dir(
            args.user_data_dir,
            args.profile_mode,
            args.session_root,
            args.source_user_data_dir,
            args.source_profile,
            args.include_sensitive_profile_data,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        parser.error(str(exc))

    window_mode = "headless" if args.headless else args.window_mode
    chrome_session = ChromeSessionManager(
        chrome_path_value=args.chrome_path,
        port=port,
        profile_plan=profile_plan,
        keep_profile=args.keep_profile,
        devtools_timeout=args.devtools_timeout,
        headless=args.headless,
        window_mode=window_mode,
        extra_args=tuple(args.chrome_arg),
    )

    def cleanup() -> None:
        chrome_session.cleanup()

    atexit.register(cleanup)

    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(signum, frame) -> None:
        cleanup()
        if callable(previous_sigterm):
            previous_sigterm(signum, frame)
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        if args.launch_mode == "eager":
            chrome_session.ensure_started()
        env = downstream_env(os.environ, port, profile_plan.user_data_dir)
        expanded_command = expand_downstream_command(command, port, profile_plan.user_data_dir)
        ensure_chrome = chrome_session.ensure_started if args.launch_mode == "lazy" else None
        return run_downstream(expanded_command, env, ensure_chrome)
    except (FileNotFoundError, NotADirectoryError, ValueError, TimeoutError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    finally:
        cleanup()

if __name__ == "__main__":
    raise SystemExit(main())

