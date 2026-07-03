import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chrome_devtools_mcp_canpoint import cli as main


class WrapperHelpersTest(unittest.TestCase):
    def test_devtools_url_uses_loopback_and_port(self):
        self.assertEqual(main.devtools_url(45678), "http://127.0.0.1:45678")

    def test_downstream_env_injects_session_values(self):
        env = main.downstream_env({"EXISTING": "1"}, 45678, Path(r"C:\Temp\profile"))

        self.assertEqual(env["EXISTING"], "1")
        self.assertEqual(env["CHROME_DEVTOOLS_URL"], "http://127.0.0.1:45678")
        self.assertEqual(env["BROWSER_URL"], "http://127.0.0.1:45678")
        self.assertEqual(env["CHROME_REMOTE_DEBUGGING_PORT"], "45678")
        self.assertEqual(env["CHROME_USER_DATA_DIR"], r"C:\Temp\profile")

    def test_build_chrome_args_includes_isolated_session_flags(self):
        config = main.ChromeSessionConfig(
            chrome_path=Path(r"C:\Chrome\chrome.exe"),
            port=45678,
            user_data_dir=Path(r"C:\Temp\profile"),
            profile_directory="Profile 1",
            headless=True,
            extra_args=("--window-size=1200,900",),
        )

        args = main.build_chrome_args(config)

        self.assertEqual(args[0], r"C:\Chrome\chrome.exe")
        self.assertIn("--remote-debugging-port=45678", args)
        self.assertIn(r"--user-data-dir=C:\Temp\profile", args)
        self.assertIn("--profile-directory=Profile 1", args)
        self.assertIn("--no-first-run", args)
        self.assertIn("--no-default-browser-check", args)
        self.assertIn("--headless=new", args)
        self.assertIn("--window-size=1200,900", args)

    def test_normalize_command_strips_separator(self):
        self.assertEqual(
            main.normalize_command(["--", "npx", "chrome-devtools-mcp"]),
            ["npx", "chrome-devtools-mcp"],
        )

    def test_normalize_command_rejects_empty_command(self):
        with self.assertRaises(ValueError):
            main.normalize_command(["--"])

    def test_expand_downstream_command_replaces_session_placeholders(self):
        command = [
            "npx",
            "chrome-devtools-mcp",
            "--browser-url={browser_url}",
            "--profile={user_data_dir}",
            "--port={port}",
        ]

        expanded = main.expand_downstream_command(command, 45678, Path(r"C:\Temp\profile"))

        self.assertEqual(
            expanded,
            [
                "npx",
                "chrome-devtools-mcp",
                "--browser-url=http://127.0.0.1:45678",
                r"--profile=C:\Temp\profile",
                "--port=45678",
            ],
        )

    def test_resolve_chrome_path_uses_explicit_existing_path(self):
        path = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"

        self.assertEqual(main.resolve_chrome_path(str(path)), path)

    def test_resolve_session_root_defaults_to_current_working_directory(self):
        self.assertEqual(
            main.resolve_session_root(None),
            (Path.cwd() / ".chrome-mcp-sessions").resolve(),
        )

    def test_default_chrome_user_data_dir_uses_localappdata_on_windows(self):
        with mock.patch.object(main.sys, "platform", "win32"), mock.patch.dict(
            main.os.environ, {"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}, clear=False
        ):
            self.assertEqual(
                main.default_chrome_user_data_dir(),
                Path(r"C:\Users\me\AppData\Local") / "Google" / "Chrome" / "User Data",
            )

    def test_resolve_source_user_data_dir_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"

            with self.assertRaises(FileNotFoundError):
                main.resolve_source_user_data_dir(str(missing))

    def test_resolve_source_profile_dir_requires_existing_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)

            with self.assertRaises(FileNotFoundError):
                main.resolve_source_profile_dir(source, "Default")

    def test_should_copy_profile_path_excludes_sensitive_by_default(self):
        self.assertFalse(main.should_copy_profile_path(Path("Default") / "Cookies", False))
        self.assertFalse(main.should_copy_profile_path(Path("Default") / "Login Data", False))
        self.assertFalse(main.should_copy_profile_path(Path("Default") / "GPUCache", True))
        self.assertTrue(main.should_copy_profile_path(Path("Default") / "Bookmarks", False))

    def test_should_copy_profile_path_includes_sensitive_with_flag(self):
        self.assertTrue(main.should_copy_profile_path(Path("Default") / "Cookies", True))
        self.assertTrue(main.should_copy_profile_path(Path("Default") / "Login Data", True))

    def test_copy_chrome_profile_copies_default_profile_and_filters_sensitive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            profile = source / "Default"
            profile.mkdir(parents=True)
            (profile / "Bookmarks").write_text("bookmarks", encoding="utf-8")
            (profile / "Cookies").write_text("cookies", encoding="utf-8")
            (profile / "GPUCache").mkdir()
            (profile / "GPUCache" / "cache.bin").write_text("cache", encoding="utf-8")
            target = root / "target"

            main.copy_chrome_profile(source, target, "Default", include_sensitive=False)

            self.assertEqual((target / "Default" / "Bookmarks").read_text(encoding="utf-8"), "bookmarks")
            self.assertFalse((target / "Default" / "Cookies").exists())
            self.assertFalse((target / "Default" / "GPUCache").exists())

    def test_copy_chrome_profile_includes_sensitive_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            profile = source / "Default"
            profile.mkdir(parents=True)
            (profile / "Bookmarks").write_text("bookmarks", encoding="utf-8")
            (profile / "Cookies").write_text("cookies", encoding="utf-8")
            target = root / "target"

            main.copy_chrome_profile(source, target, "Default", include_sensitive=True)

            self.assertEqual((target / "Default" / "Cookies").read_text(encoding="utf-8"), "cookies")

    def test_select_user_data_dir_inherit_uses_source_root_without_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "User Data"
            (source / "Default").mkdir(parents=True)

            selection = main.select_user_data_dir(
                None,
                "inherit",
                None,
                str(source),
                "Default",
                False,
            )

            self.assertEqual(selection.user_data_dir, source.resolve())
            self.assertFalse(selection.generated_session_dir)
            self.assertEqual(selection.profile_directory, "Default")

    def test_select_user_data_dir_copy_creates_generated_profile_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            (source / "Default" / "Bookmarks").write_text("bookmarks", encoding="utf-8")
            session_root = root / "sessions"

            selection = main.select_user_data_dir(
                None,
                "copy",
                str(session_root),
                str(source),
                "Default",
                False,
            )

            self.assertTrue(selection.generated_session_dir)
            self.assertEqual(selection.profile_directory, "Default")
            self.assertEqual(selection.user_data_dir.parent, session_root.resolve())
            self.assertEqual(
                (selection.user_data_dir / "Default" / "Bookmarks").read_text(encoding="utf-8"),
                "bookmarks",
            )

    @unittest.skipUnless(os.name == "nt", "Windows-specific process tree cleanup")
    def test_terminate_process_uses_taskkill_process_tree_on_windows(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.pid = 1234

        with mock.patch.object(main.subprocess, "run") as run:
            main.terminate_process(process)

        run.assert_called_once_with(
            ["taskkill", "/PID", "1234", "/T", "/F"],
            stdout=main.subprocess.DEVNULL,
            stderr=main.subprocess.DEVNULL,
            check=False,
        )
        process.wait.assert_called_once()

    def test_remove_directory_with_retries_stops_after_directory_is_gone(self):
        path = mock.Mock()
        path.exists.side_effect = [True, False]

        with mock.patch.object(main.shutil, "rmtree") as rmtree, mock.patch.object(
            main.time, "sleep"
        ) as sleep:
            main.remove_directory_with_retries(path, attempts=3, delay_seconds=0.01)

        self.assertEqual(rmtree.call_count, 2)
        sleep.assert_called_once_with(0.01)

    def test_resolve_downstream_command_prefers_windows_cmd_shim(self):
        def fake_which(name):
            return {"npx.cmd": r"C:\node\npx.cmd"}.get(name)

        with mock.patch.object(main.os, "name", "nt"), mock.patch.dict(
            main.os.environ, {"PATHEXT": ".COM;.EXE;.BAT;.CMD"}, clear=False
        ), mock.patch.object(main.shutil, "which", side_effect=fake_which):
            self.assertEqual(
                main.resolve_downstream_command(["npx", "--version"]),
                [r"C:\node\npx.cmd", "--version"],
            )

    def test_resolve_downstream_command_leaves_explicit_paths_unchanged(self):
        with mock.patch.object(main.os, "name", "nt"), mock.patch.object(
            main.shutil, "which"
        ) as which:
            command = [r"C:\Tools\npx.cmd", "--version"]

            self.assertEqual(main.resolve_downstream_command(command), command)
            which.assert_not_called()

    def test_bridge_stream_forwards_small_message_before_eof(self):
        source_read, source_write = os.pipe()
        target_read, target_write = os.pipe()
        with os.fdopen(source_read, "rb", buffering=0) as source, os.fdopen(
            target_write, "wb", buffering=0
        ) as target:
            thread = threading.Thread(target=main.bridge_stream, args=(source, target))
            thread.start()
            message = b"Content-Length: 2\r\n\r\n{}"
            os.write(source_write, message)
            forwarded = os.read(target_read, len(message))
            os.close(source_write)
            thread.join(timeout=2)
            os.close(target_read)

        self.assertEqual(forwarded, message)

    @unittest.skipUnless(os.name == "nt", "Windows-specific Chrome cleanup")
    def test_terminate_chrome_profile_processes_filters_by_profile_path(self):
        with mock.patch.object(main.subprocess, "run") as run:
            main.terminate_chrome_profile_processes(Path(r"C:\Temp\profile"))

        args, kwargs = run.call_args
        self.assertTrue(args[0][0].lower().endswith(r"powershell.exe"))
        self.assertEqual(args[0][1:3], ["-NoProfile", "-Command"])
        self.assertEqual(kwargs["env"]["CHROME_MCP_PROFILE_MARKER"], r"C:\Temp\profile")
        self.assertFalse(kwargs["check"])


if __name__ == "__main__":
    unittest.main()
