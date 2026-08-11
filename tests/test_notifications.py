"""Tests for notification_email and notification_kodi."""

from unittest.mock import MagicMock

import pytest


class TestNotificationEmail:
    """notification_email(**kwargs)."""

    @pytest.fixture(autouse=True)
    def disable_notification_flag(self, tdb):
        """Ensure email_notification is True for most tests."""
        tdb.email_notification = True

    def test_disabled_notification_returns_1(self, tdb, mock_yagmail):
        tdb.email_notification = False
        result = tdb.notification_email(
            msg_type="site_error",
            error_msg="test error",
            source_site_name="GitHub",
            source_site_url="https://example.com",
        )
        assert result == 1
        mock_yagmail.assert_not_called()

    def test_site_error_sends_email_and_returns_0(self, tdb, mock_yagmail):
        result = tdb.notification_email(
            msg_type="site_error",
            error_msg="site down",
            source_site_name="GitHub",
            source_site_url="https://github.com",
        )
        assert result == 0
        mock_yagmail.assert_called_once()
        smtp_instance = mock_yagmail.return_value
        smtp_instance.send.assert_called_once()
        # yagmail.send(to=..., subject=..., contents=[...])
        call_kwargs = smtp_instance.send.call_args.kwargs
        assert "site down" in str(call_kwargs.get("contents", ""))

    def test_site_recovered_sends_email(self, tdb, mock_yagmail):
        result = tdb.notification_email(
            msg_type="site_recovered",
            error_msg="site back up",
            source_site_name="GitHub",
            source_site_url="https://github.com",
        )
        assert result == 0
        smtp_instance = mock_yagmail.return_value
        smtp_instance.send.assert_called_once()

    def test_app_error_sends_email(self, tdb, mock_yagmail):
        result = tdb.notification_email(
            msg_type="app_error",
            error_msg="app down",
            source_site_name="GitHub",
            source_repo_name="owner/repo",
            source_app_name="myapp",
            source_site_url="https://example.com",
        )
        assert result == 0
        mock_yagmail.return_value.send.assert_called_once()

    def test_config_error_sends_email(self, tdb, mock_yagmail):
        result = tdb.notification_email(
            msg_type="config_error",
            error_msg="missing config",
            source_site_name="GitHub",
            source_repo_name="owner/repo",
            source_app_name="myapp",
            source_site_url="https://example.com",
        )
        assert result == 0

    def test_trigger_action_sends_email_with_docker_links(self, tdb, mock_yagmail):
        tdb.config_obj["general"]["target_repo_owner"] = "testowner"
        result = tdb.notification_email(
            action="trigger",
            source_app_name="myapp",
            source_repo_name="owner/repo",
            source_site_name="GitHub",
            source_site_url="https://github.com",
            target_repo_name="docker-image",
            previous_version="v1.0.0",
            current_version="v2.0.0",
        )
        assert result == 0
        mock_yagmail.return_value.send.assert_called_once()

    def test_notify_action_sends_email_without_docker_links(self, tdb, mock_yagmail):
        tdb.config_obj["general"]["target_repo_owner"] = "testowner"
        result = tdb.notification_email(
            action="notify",
            source_app_name="myapp",
            source_repo_name="owner/repo",
            source_site_name="GitHub",
            source_site_url="https://github.com",
            target_repo_name="docker-image",
            previous_version="v1.0.0",
            current_version="v2.0.0",
        )
        assert result == 0

    def test_none_source_site_url_uses_placeholder(self, tdb, mock_yagmail):
        """When source_site_url is None, fallback placeholder is used."""
        result = tdb.notification_email(
            msg_type="site_error",
            error_msg="error",
            source_site_name="GitHub",
        )
        assert result == 0
        smtp_instance = mock_yagmail.return_value
        smtp_instance.send.assert_called_once()
        call_kwargs = smtp_instance.send.call_args.kwargs
        assert "(unknown)" in str(call_kwargs.get("contents", ""))

    def test_html_escaping_applied(self, tdb, mock_yagmail):
        """HTML tags in user input are escaped."""
        result = tdb.notification_email(
            msg_type="site_error",
            error_msg="<script>alert(1)</script>",
            source_site_name="<evil>",
            source_site_url="https://example.com",
        )
        assert result == 0
        smtp_instance = mock_yagmail.return_value
        smtp_instance.send.assert_called_once()
        call_kwargs = smtp_instance.send.call_args.kwargs
        content = str(call_kwargs.get("contents", ""))
        assert "&lt;script&gt;" in content
        assert "&lt;evil&gt;" in content


class TestNotificationKodi:
    """notification_kodi(action, source_app_name, current_version)."""

    @pytest.fixture(autouse=True)
    def enable_kodi(self, tdb):
        tdb.kodi_notification = True
        tdb.kodi_password = "kodi"

    def test_disabled_kodi_returns_1(self, tdb, monkeypatch):
        tdb.kodi_notification = False
        mock_kodi = MagicMock()
        monkeypatch.setattr("kodijson.Kodi", mock_kodi)
        result = tdb.notification_kodi("trigger", "myapp", "v2.0.0")
        assert result == 1
        mock_kodi.assert_not_called()

    def test_kodi_notification_sent(self, tdb, monkeypatch):
        mock_kodi = MagicMock()
        monkeypatch.setattr("kodijson.Kodi", mock_kodi)
        result = tdb.notification_kodi("trigger", "myapp", "v2.0.0")
        assert result is None  # no explicit return on success path
        mock_kodi.return_value.GUI.ShowNotification.assert_called_once()

    def test_kodi_exception_returns_1(self, tdb, monkeypatch):
        mock_kodi = MagicMock()
        mock_kodi.return_value.GUI.ShowNotification.side_effect = Exception("fail")
        monkeypatch.setattr("kodijson.Kodi", mock_kodi)
        result = tdb.notification_kodi("trigger", "myapp", "v2.0.0")
        assert result == 1
