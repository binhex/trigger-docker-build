"""Additional coverage tests for remaining uncovered branches."""

import json
import os
from unittest.mock import MagicMock

import pytest


class TestCreateConfig:
    """create_config()."""

    def test_create_config_writes_file(self, tdb):
        """create_config validates and writes the config file."""
        import configobj

        configspec_path = os.path.join(os.path.dirname(__file__), "..", "configs", "configspec.ini")
        # Load configspec as a proper ConfigObj
        spec = configobj.ConfigObj(configspec_path, list_values=False, encoding="UTF-8")
        tdb.config_obj.configspec = spec
        tdb.config_obj.filename = tdb.config_ini

        # create_config should not raise
        tdb.create_config()
        assert os.path.exists(tdb.config_ini)


class TestAppLogging:
    """app_logging() — creates logger and handler."""

    def test_returns_logger_and_handler(self, tdb, tmp_path):
        log_file = str(tmp_path / "test.log")
        tdb.app_log_file = log_file

        tdb.config_obj["general"]["log_level"] = "DEBUG"

        result = tdb.app_logging()
        assert "logger" in result
        assert "handler" in result
        assert result["logger"] is not None

    def test_defaults_to_warning_on_unknown_level(self, tdb, tmp_path):
        log_file = str(tmp_path / "test.log")
        tdb.app_log_file = log_file
        tdb.config_obj["general"]["log_level"] = "UNKNOWN"

        import logging

        result = tdb.app_logging()
        logger = result["logger"]
        # Should default to WARNING
        assert logger.level <= logging.WARNING

    def test_info_level(self, tdb, tmp_path):
        log_file = str(tmp_path / "test.log")
        tdb.app_log_file = log_file
        tdb.config_obj["general"]["log_level"] = "INFO"

        import logging

        result = tdb.app_logging()
        assert result["logger"].level == logging.INFO

    def test_case_insensitive_log_level(self, tdb, tmp_path):
        """Log level comparison is case-insensitive."""
        log_file = str(tmp_path / "test.log")
        tdb.app_log_file = log_file
        tdb.config_obj["general"]["log_level"] = "debug"  # lowercase

        import logging

        result = tdb.app_logging()
        assert result["logger"].level == logging.DEBUG


class TestHttpClientBranches:
    """http_client() edge cases and error branches."""

    def test_missing_url_returns_error(self, tdb, mock_http):
        return_code, status_code, content = tdb.http_client(
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_missing_user_agent_returns_error(self, tdb, mock_http):
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            request_type="get",
        )
        assert return_code == 1

    def test_missing_request_type_returns_error(self, tdb, mock_http):
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
        )
        assert return_code == 1

    def test_empty_kwargs_returns_error(self, tdb, mock_http):
        return_code, status_code, content = tdb.http_client()
        assert return_code == 1

    def test_connection_error_returns_1(self, tdb, mock_http):
        from requests.exceptions import ConnectionError

        mock_http.get.side_effect = ConnectionError("connection refused")
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_too_many_redirects_returns_1(self, tdb, mock_http):
        from requests.exceptions import TooManyRedirects

        mock_http.get.side_effect = TooManyRedirects("too many")
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_read_timeout_returns_1(self, tdb, mock_http):
        from requests.exceptions import ReadTimeout

        mock_http.get.side_effect = ReadTimeout("read timeout")
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_generic_request_exception_returns_1(self, tdb, mock_http):
        from requests.exceptions import RequestException

        mock_http.get.side_effect = RequestException("generic")
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_401_raises_http_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 401
        mock_http.get.return_value.content = b"unauthorized"
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_404_raises_http_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 404
        mock_http.get.return_value.content = b"not found"
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_422_raises_http_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 422
        mock_http.get.return_value.content = b"unprocessable"
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_other_non_2xx_raises_http_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"server error"
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_connect_timeout_returns_1(self, tdb, mock_http):
        from requests.exceptions import ConnectTimeout

        mock_http.get.side_effect = ConnectTimeout("timeout")
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
        )
        assert return_code == 1

    def test_additional_header_none_skipped(self, tdb, mock_http):
        """None additional_header should be safely skipped (no TypeError)."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
            additional_header=None,
        )
        assert return_code == 0

    def test_post_request_with_data(self, tdb, mock_http):
        """POST request with data_payload works."""
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"created"
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="post",
            data_payload='{"key":"value"}',
        )
        assert return_code == 0


class TestCheckSiteMorePaths:
    """check_site branch coverage."""

    @pytest.fixture(autouse=True)
    def reset_state(self, tdb):
        tdb._site_down_state.clear()

    def test_new_site_down_sends_error_and_records_state(self, tdb, mock_http, monkeypatch):
        mock_notify = MagicMock()
        monkeypatch.setattr(tdb, "notification_email", mock_notify)
        # Disable sleep to avoid retry-loop delay
        monkeypatch.setattr(tdb.time, "sleep", MagicMock())

        # Make HTTP fail
        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"error"

        site_down = tdb.check_site(
            url="https://example.com",
            user_agent="agent/1.0",
            site_name="Example",
        )
        assert site_down is True
        assert tdb._site_down_state["Example"]["is_down"] is True
        # Should have sent a site_error email
        error_calls = [c for c in mock_notify.call_args_list if c.kwargs.get("msg_type") == "site_error"]
        assert len(error_calls) == 1


class TestMonitorSitesMore:
    """Additional monitor_sites branch coverage."""

    @pytest.fixture(autouse=True)
    def reset_state(self, tdb):
        tdb._app_down_counters.clear()
        tdb._site_down_state.clear()

    def test_aor_site_app_fetch(self, tdb, mock_http, monkeypatch):
        """Test AOR site type in monitor_sites."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

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

    def test_aur_site_app_fetch(self, tdb, mock_http, monkeypatch):
        """Test AUR site type in monitor_sites."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": [{"Version": "1.0.0-1"}]})

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "aur",
                "source_app_name": "yay",
                "source_repo_name": "archlinux",
                "target_repo_name": "docker-image",
                "action": "notify",
            }
        ]

        tdb.monitor_sites()

    def test_pypi_site_app_fetch(self, tdb, mock_http, monkeypatch):
        """Test PyPI site type in monitor_sites."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"info": {"version": "2.0.0"}})

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "pypi",
                "source_app_name": "requests",
                "source_repo_name": "pypi",
                "target_repo_name": "docker-image",
                "action": "notify",
            }
        ]

        tdb.monitor_sites()

    def test_regex_minecraftbedrock_app(self, tdb, mock_http, monkeypatch):
        """Test regex site type with minecraftbedrock app."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            {
                "result": {
                    "links": [
                        {
                            "downloadType": "serverBedrockLinux",
                            "downloadUrl": "https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-1.21.90.4.zip",
                        }
                    ]
                }
            }
        )

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "regex",
                "source_app_name": "minecraftbedrock",
                "source_repo_name": "minecraft",
                "target_repo_name": "docker-image",
                "action": "notify",
            }
        ]

        tdb.monitor_sites()

    def test_regex_minecraftserver_app(self, tdb, mock_http, monkeypatch):
        """Test regex site type with minecraftserver app."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        # First call for version manifest
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"latest": {"release": "1.21.4"}})

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "regex",
                "source_app_name": "minecraftserver",
                "source_repo_name": "minecraft",
                "target_repo_name": "docker-image",
                "action": "notify",
            }
        ]

        tdb.monitor_sites()

    def test_regex_unknown_app_skipped(self, tdb, mock_http, monkeypatch):
        """Unknown regex app is skipped."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "regex",
                "source_app_name": "unknown_app",
                "source_repo_name": "test",
                "target_repo_name": "docker-image",
                "action": "notify",
            }
        ]

        # Should not raise
        tdb.monitor_sites()


class TestOndemandStart:
    """ondemand_start() and scheduler-related functions."""

    def test_ondemand_start_calls_monitor_sites(self, tdb, monkeypatch):
        """ondemand_start calls monitor_sites."""
        mock_monitor = MagicMock()
        monkeypatch.setattr(tdb, "monitor_sites", mock_monitor)

        tdb.ondemand_start()
        mock_monitor.assert_called_once()
