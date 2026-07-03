from __future__ import annotations

import argparse
import atexit
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
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


@dataclass(frozen=True)
class ProfileSelection:
    user_data_dir: Path
    generated_session_dir: bool
    profile_directory: str | None


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
    if config.headless:
        args.append("--headless=new")
    args.extend(config.extra_args)
    return args


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


def resolve_source_user_data_dir(value: str | None) -> Path:
    source_user_data_dir = Path(value).expanduser() if value else default_chrome_user_data_dir()
    source_user_data_dir = source_user_data_dir.resolve()
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


def select_user_data_dir(
    user_data_dir: str | None,
    profile_mode: str,
    session_root: str | None,
    source_user_data_dir: str | None,
    source_profile: str,
    include_sensitive_profile_data: bool,
) -> ProfileSelection:
    if user_data_dir:
        return ProfileSelection(Path(user_data_dir).expanduser().resolve(), False, None)

    if profile_mode == "isolated":
        return ProfileSelection(resolve_session_root(session_root) / uuid.uuid4().hex, True, None)

    source_root = resolve_source_user_data_dir(source_user_data_dir)
    resolve_source_profile_dir(source_root, source_profile)
    if profile_mode == "inherit":
        return ProfileSelection(source_root, False, source_profile)

    if profile_mode == "copy":
        target_root = resolve_session_root(session_root) / uuid.uuid4().hex
        copy_chrome_profile(
            source_root,
            target_root,
            source_profile,
            include_sensitive_profile_data,
        )
        copy_chrome_local_state(source_root, target_root, include_sensitive_profile_data)
        return ProfileSelection(target_root, True, source_profile)

    raise ValueError(f"Unknown profile mode: {profile_mode}")


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


def run_downstream(command: Sequence[str], env: Mapping[str, str]) -> int:
    downstream = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        env=dict(env),
    )

    stdin_thread = threading.Thread(
        target=bridge_stream,
        args=(sys.stdin.buffer, downstream.stdin),
        daemon=True,
    )
    stdout_thread = threading.Thread(
        target=bridge_stream,
        args=(downstream.stdout, sys.stdout.buffer),
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
        description="Start a session-local Chrome instance and run a downstream MCP command."
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
        "--headless",
        action="store_true",
        help="Start Chrome in headless mode.",
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
        chrome_path = resolve_chrome_path(args.chrome_path)
        profile_selection = select_user_data_dir(
            args.user_data_dir,
            args.profile_mode,
            args.session_root,
            args.source_user_data_dir,
            args.source_profile,
            args.include_sensitive_profile_data,
        )
        user_data_dir = profile_selection.user_data_dir
        generated_session_dir = profile_selection.generated_session_dir
        profile_directory = profile_selection.profile_directory
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        parser.error(str(exc))

    port = find_free_port()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    config = ChromeSessionConfig(
        chrome_path=chrome_path,
        port=port,
        user_data_dir=user_data_dir,
        profile_directory=profile_directory,
        headless=args.headless,
        extra_args=tuple(args.chrome_arg),
    )

    chrome = subprocess.Popen(
        build_chrome_args(config),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def cleanup() -> None:
        terminate_process(chrome)
        terminate_chrome_profile_processes(user_data_dir)
        if generated_session_dir and not args.keep_profile:
            remove_directory_with_retries(user_data_dir)

    atexit.register(cleanup)

    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(signum, frame) -> None:
        cleanup()
        if callable(previous_sigterm):
            previous_sigterm(signum, frame)
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        wait_for_devtools(port, args.devtools_timeout)
        env = downstream_env(os.environ, port, user_data_dir)
        expanded_command = expand_downstream_command(command, port, user_data_dir)
        return run_downstream(expanded_command, env)
    except KeyboardInterrupt:
        return 130
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

