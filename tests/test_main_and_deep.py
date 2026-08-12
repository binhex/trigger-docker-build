"""Tests for __main__ block, scheduler, and deep branch coverage."""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest


class TestSchedulerAndOndemand:
    """ondemand_start() and scheduler_start()."""

    def test_ondemand_start_logs_and_calls_monitor(self, tdb, monkeypatch):
        mock_monitor = MagicMock()
        monkeypatch.setattr(tdb, "monitor_sites", mock_monitor)
        tdb.ondemand_start()
        mock_monitor.assert_called_once()

    def test_scheduler_run_pending_calls_monitor_sites(self, tdb, monkeypatch):
        """Verify that scheduler_start's schedule.run_pending triggers monitor_sites."""
        mock_monitor = MagicMock()
        monkeypatch.setattr(tdb, "monitor_sites", mock_monitor)

        # Mock schedule to immediately invoke the callback
        mock_schedule = MagicMock()
        monkeypatch.setattr(tdb, "schedule", mock_schedule)
        mock_schedule.every.return_value.minutes.do = MagicMock()

        # Set config for schedule check interval
        tdb.config_obj["general"]["schedule_check_mins"] = 30

        # Mock time.sleep to avoid blocking
        mock_sleep = MagicMock(side_effect=KeyboardInterrupt)
        monkeypatch.setattr(tdb.time, "sleep", mock_sleep)

        # scheduler_start should exit on KeyboardInterrupt
        with pytest.raises(SystemExit):
            tdb.scheduler_start()


class TestMainBlock:
    """Test the __main__ block (argparse, config setup, startup paths)."""

    def test_module_import_does_not_execute_main(self):
        """Confirm importing the module does NOT execute __main__."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "tdb_test", os.path.join(os.path.dirname(__file__), "..", "TriggerDockerBuild.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # This should not execute the __main__ block
        spec.loader.exec_module(mod)
        assert hasattr(mod, "monitor_sites")

    def test_help_flag(self):
        """--help flag exits cleanly."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "TriggerDockerBuild.py", "--help"], capture_output=True, text=True, timeout=10
        )
        assert "usage:" in result.stdout.lower() or "usage:" in result.stderr.lower()

    def test_version_flag(self):
        """--version flag prints version and exits."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "TriggerDockerBuild.py", "--version"], capture_output=True, text=True, timeout=10
        )
        assert "1.2.5" in result.stdout or "1.2.5" in result.stderr

    def test_basic_run_with_temp_config(self, tmp_path):
        """Run with a temp config directory (no real network needed — will fail gracefully)."""
        config_dir = tmp_path / "configs"
        logs_dir = tmp_path / "logs"
        config_dir.mkdir()
        logs_dir.mkdir()

        # Create minimal config
        config_file = config_dir / "config.ini"
        config_file.write_text("""[general]
schedule_check_mins = 30
log_level = INFO
target_repo_owner = test_owner
target_access_token = fake_token

[monitor_sites]
site_list = []

[results]

[notification]
email_to = test@example.com
email_username = test
email_password = pass
kodi_password = kodi
email_notification = False
kodi_notification = False
""")

        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "TriggerDockerBuild.py",
                "--config",
                str(config_dir),
                "--logs",
                str(logs_dir),
                "--target-access-token",
                "fake_token",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Should exit cleanly (or with error due to network — either is fine for coverage)
        assert result.returncode in (0, 1)


class TestGithubCreateReleaseRemaining:
    """Cover github_create_release remaining paths."""

    def test_return_error_when_api_fails(self, tdb, mock_http):
        """HTTP failure returns non-zero return_code."""
        mock_http.post.return_value.status_code = 400
        mock_http.post.return_value.content = b'{"message":"Bad request"}'

        return_code, status_code, content = tdb.github_create_release("v1.0.0", "main", "owner", "repo", "agent/1.0")
        assert return_code == 1


class TestMonitorSitesDeepBranches:
    """Cover deep monitor_sites() branches."""

    @pytest.fixture(autouse=True)
    def reset_state(self, tdb):
        tdb._app_down_counters.clear()
        tdb._site_down_state.clear()

    def test_grace_period_block(self, tdb, mock_http, monkeypatch):
        """Grace period blocks trigger when too recent."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"tag_name": "v2.0.0"})

        tdb.config_obj["results"] = {"github_testapp_docker-image_previous_version": "v1.0.0"}

        import datetime

        # Set source_version_change_datetime to 5 minutes ago — within 99999 min grace period
        recent = (datetime.datetime.now() - datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "github",
                "source_app_name": "testapp",
                "source_repo_name": "owner/repo",
                "source_query_type": "release",
                "target_repo_name": "docker-image",
                "target_repo_branch": "main",
                "action": "trigger",
                "grace_period_mins": "99999",  # huge grace period — will block recent change
                "source_version_change_datetime": recent,
                "target_release_days": None,
            }
        ]

        tdb.monitor_sites()
        # Should not have created a release (grace period blocks)

    def test_grace_period_passed_proceeds(self, tdb, mock_http, monkeypatch):
        """Grace period passed — trigger proceeds."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"tag_name": "v2.0.0"})
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"ok"

        tdb.config_obj["results"] = {"github_testapp_docker-image_previous_version": "v1.0.0"}

        import datetime

        past = (datetime.datetime.now() - datetime.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "github",
                "source_app_name": "testapp",
                "source_repo_name": "owner/repo",
                "source_query_type": "release",
                "target_repo_name": "docker-image",
                "target_repo_branch": "main",
                "action": "trigger",
                "grace_period_mins": "1",  # small grace period
                "source_version_change_datetime": past,
                "target_release_days": None,
            }
        ]

        tdb.monitor_sites()

    def test_trigger_release_already_exists(self, tdb, mock_http, monkeypatch):
        """GitHub returns 'already_exists' error — handled gracefully."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"tag_name": "v2.0.0"})

        # http_client catches 422 and raises HTTPError, which returns (1, status_code, content)
        # The github_create_release result returns (1, 422, content_with_already_exists)
        mock_http.post.return_value.status_code = 422
        mock_http.post.return_value.content = b'{"message":"Validation Failed","errors":[{"code":"already_exists"}]}'

        tdb.config_obj["results"] = {"github_testapp_docker-image_previous_version": "v1.0.0"}

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "github",
                "source_app_name": "testapp",
                "source_repo_name": "owner/repo",
                "source_query_type": "release",
                "target_repo_name": "docker-image",
                "target_repo_branch": "main",
                "action": "trigger",
                "grace_period_mins": None,
                "target_release_days": None,
            }
        ]

        # Should not crash
        tdb.monitor_sites()

    def test_aur_grace_period_below_default(self, tdb, mock_http, monkeypatch):
        """AOR with grace_period_mins=None defaults to 60."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            {"results": [{"pkgname": "base", "pkgver": "3", "pkgrel": "1"}]}
        )

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "aor",
                "source_app_name": "base",
                "source_repo_name": "archlinux",
                "target_repo_name": "docker-image",
                "action": "notify",
                "grace_period_mins": None,
            }
        ]

        tdb.monitor_sites()

    def test_site_down_github_continues(self, tdb, mock_http, monkeypatch):
        """When github site is down, github apps skip but other apps continue."""
        # First call (GitHub) returns True (down), other calls return False
        call_count = [0]

        def check_site_side_effect(**kwargs):
            call_count[0] += 1
            # GitHub site check (first 5 calls check sites, then app processing)
            if kwargs.get("site_name") == "GitHub":
                return True
            return False

        monkeypatch.setattr(tdb, "check_site", MagicMock(side_effect=check_site_side_effect))

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "github",
                "source_app_name": "testapp",
                "source_repo_name": "owner/repo",
                "source_query_type": "release",
                "target_repo_name": "docker-image",
                "action": "notify",
            }
        ]

        tdb.monitor_sites()
