"""Tests for monitor_sites."""

import json
from unittest.mock import MagicMock

import pytest


class TestMonitorSites:
    """monitor_sites() — the main orchestrator."""

    @pytest.fixture
    def mock_check_site(self, monkeypatch, tdb):
        """Mock check_site to always return False (site is up)."""
        mock = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock)
        return mock

    @pytest.fixture(autouse=True)
    def mock_scheduler_stuff(self, tdb):
        """Ensure app_down_counters is reset."""
        tdb._app_down_counters.clear()
        tdb._site_down_state.clear()
        tdb.email_notification = True

    def test_empty_site_list_completes_without_error(self, tdb, monkeypatch):
        """Empty site list should just write last_check and return."""
        tdb.config_obj["monitor_sites"]["site_list"] = []

        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        # Should not raise
        tdb.monitor_sites()
        assert tdb.config_obj["general"]["last_check"] != ""

    def test_github_app_processed(self, tdb, mock_http, monkeypatch):
        """A github site entry is processed end-to-end."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        mock_yag = MagicMock()
        monkeypatch.setattr("yagmail.SMTP", mock_yag)

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"tag_name": "v2.0.0"})
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"ok"

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "github",
                "source_app_name": "testapp",
                "source_repo_name": "owner/repo",
                "source_query_type": "release",
                "target_repo_name": "docker-image",
                "target_repo_branch": "main",
                "action": "trigger",
                "source_branch_name": None,
                "target_release_days": None,
                "grace_period_mins": None,
                "source_version_change_datetime": None,
            }
        ]

        tdb.monitor_sites()

    def test_unknown_source_site_skipped(self, tdb, mock_http, monkeypatch):
        """Unknown source_site_name is skipped with warning."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "unknown_source",
                "source_app_name": "testapp",
                "action": "notify",
            }
        ]

        # Should not raise
        tdb.monitor_sites()

    def test_notify_action_triggers_notification(self, tdb, mock_http, monkeypatch):
        """Notify action sends notification without creating release."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        mock_notify = MagicMock()
        monkeypatch.setattr(tdb, "notification_email", mock_notify)

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"tag_name": "v2.0.0"})

        # Set a previous version different from current to trigger notification
        tdb.config_obj["results"] = {"github_testapp_docker-image_previous_version": "v1.0.0"}

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
        mock_notify.assert_called()

    def test_config_error_missing_branch(self, tdb, mock_http, monkeypatch):
        """Missing target_repo_branch with trigger action sends config_error."""
        mock_check = MagicMock(return_value=False)
        monkeypatch.setattr(tdb, "check_site", mock_check)

        mock_notify = MagicMock()
        monkeypatch.setattr(tdb, "notification_email", mock_notify)

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "github",
                "source_app_name": "testapp",
                "source_repo_name": "owner/repo",
                "source_query_type": "release",
                "target_repo_name": "docker-image",
                "action": "trigger",
            }
        ]

        tdb.monitor_sites()
        config_errors = [c for c in mock_notify.call_args_list if c.kwargs.get("msg_type") == "config_error"]
        assert len(config_errors) >= 1
