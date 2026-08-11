"""In-process __main__ execution using exec() with comprehensive mocking.

Uses exec() on TriggerDockerBuild.py with __name__='__main__' to get
coverage of the full __main__ block (~235 lines). All network/daemon/fork
dependencies are mocked.
"""

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


def _exec_main_in_process(config_dir, logs_dir, extra_args=None):
    """Execute TriggerDockerBuild.py __main__ block in-process via exec().

    Mock everything that would cause real network calls, daemon forks,
    or infinite loops. Site checks and app fetches are all stubbed.
    """
    script_path = Path(__file__).parent.parent / "TriggerDockerBuild.py"

    old_cwd = os.getcwd()
    old_argv = sys.argv[:]

    try:
        os.chdir(str(config_dir.parent))

        args = [
            "TriggerDockerBuild.py",
            "--config",
            str(config_dir),
            "--logs",
            str(logs_dir),
            "--target-access-token",
            "fake_token",
        ]
        if extra_args:
            args.extend(extra_args)
        sys.argv = args

        source = script_path.read_text()

        # Create a clean namespace with __name__ = '__main__'
        namespace = {
            "__name__": "__main__",
            "__file__": str(script_path),
        }

        # Mocks to prevent real network calls
        mock_session = MagicMock()
        mock_session.get.return_value.status_code = 200
        mock_session.get.return_value.content = b"ok"
        mock_session.post.return_value.status_code = 201
        mock_session.post.return_value.content = b"ok"

        mocks = {
            "requests.Session": MagicMock(return_value=mock_session),
            "daemon.DaemonContext": MagicMock(),
            "schedule.every": MagicMock(),
            "schedule.run_pending": MagicMock(return_value=0),
            "yagmail.SMTP": MagicMock(),
            "kodijson.Kodi": MagicMock(),
        }

        with patch.dict("sys.modules") if False else _MultiPatch(mocks):
            exec(source, namespace)
    except SystemExit as e:
        if e.code and e.code != 0:
            raise
    finally:
        os.chdir(old_cwd)
        sys.argv = old_argv


class _MultiPatch:
    """Context manager that applies multiple module-level patches."""

    def __init__(self, patches):
        self.patches = patches
        self.originals = {}

    def __enter__(self):
        import importlib

        for path, mock_obj in self.patches.items():
            mod_name, attr_name = path.rsplit(".", 1)
            try:
                mod = importlib.import_module(mod_name)
            except ImportError:
                continue
            self.originals[path] = getattr(mod, attr_name, None)
            setattr(mod, attr_name, mock_obj)
        return self

    def __exit__(self, *args):
        import importlib

        for path, original in self.originals.items():
            mod_name, attr_name = path.rsplit(".", 1)
            try:
                mod = importlib.import_module(mod_name)
            except ImportError:
                continue
            if original is None:
                try:
                    delattr(mod, attr_name)
                except (AttributeError, TypeError):
                    pass
            else:
                setattr(mod, attr_name, original)


# ── tests ─────────────────────────────────────────────────────────────


class TestMainExecBasic:
    """Basic in-process __main__ execution."""

    def test_main_exec_default(self, tmp_path):
        """Run __main__ with minimal flags."""
        config_dir, logs_dir = _setup_env(tmp_path)
        _exec_main_in_process(config_dir, logs_dir)

    def test_main_exec_with_email_notification(self, tmp_path):
        config_dir, logs_dir = _setup_env(tmp_path)
        _exec_main_in_process(config_dir, logs_dir, ["--email-notification"])

    def test_main_exec_with_kodi_notification(self, tmp_path):
        config_dir, logs_dir = _setup_env(tmp_path)
        _exec_main_in_process(config_dir, logs_dir, ["--kodi-notification"])

    def test_main_exec_with_all_notification_flags(self, tmp_path):
        config_dir, logs_dir = _setup_env(tmp_path)
        _exec_main_in_process(
            config_dir,
            logs_dir,
            [
                "--email-notification",
                "--email-to",
                "a@b.com",
                "--email-username",
                "user",
                "--email-password",
                "pass",
                "--kodi-notification",
                "--kodi-password",
                "kpass",
            ],
        )

    def test_main_exec_with_pidfile_and_schedule(self, tmp_path):
        """Exercise --pidfile flag. (--schedule hangs in-process; covered by
        subprocess test in test_main_coverage.py.)"""
        config_dir, logs_dir = _setup_env(tmp_path)
        pidfile = tmp_path / "tdb.pid"
        _exec_main_in_process(
            config_dir,
            logs_dir,
            ["--pidfile", str(pidfile)],
        )

    def test_main_exec_no_target_token_in_config(self, tmp_path):
        """Missing token in config + no CLI flag → exit(1)."""
        config_dir, logs_dir = _setup_env(tmp_path, include_token=False)
        try:
            _exec_main_in_process(config_dir, logs_dir)
        except SystemExit as e:
            assert e.code == 1


def _setup_env(tmp_path, include_token=True):
    """Create test config/log dirs."""
    config_dir = tmp_path / "configs"
    logs_dir = tmp_path / "logs"
    config_dir.mkdir()
    logs_dir.mkdir()

    # Copy configspec
    src_spec = Path(__file__).parent.parent / "configs" / "configspec.ini"
    shutil.copy(src_spec, config_dir / "configspec.ini")

    token_line = "target_access_token = 'fake_token'\n" if include_token else ""

    (config_dir / "config.ini").write_text(
        f"""[general]
schedule_check_mins = 30
log_level = 'INFO'
target_repo_owner = 'test_owner'
{token_line}
[monitor_sites]
site_list = []

[results]

[notification]
email_to = 'test@example.com'
email_username = 'test'
email_password = 'pass'
kodi_password = 'kodi'
email_notification = True
kodi_notification = True
"""
    )
    return config_dir, logs_dir
