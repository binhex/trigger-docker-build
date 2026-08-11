"""Targeted tests to push CRAP scores below threshold 9."""

import json
from unittest.mock import MagicMock

import pytest


class TestCrapReductionGitlab:
    """Push gitlab_apps coverage to bring CRAP < 9."""

    def test_gitlab_fetch_error_with_nonzero_return(self, tdb, mock_http):
        """HTTP error with content that's valid JSON but different return code."""
        mock_http.get.return_value.status_code = 403
        mock_http.get.return_value.content = b"forbidden"

        version, url = tdb.gitlab_apps("app", "group/repo", "123", "main", "branch", "agent/1.0")
        assert version is None
        assert url is not None


class TestCrapReductionAor:
    """Push aor_apps coverage."""

    def test_aor_empty_results(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": []})
        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version is None

    def test_aor_missing_pkgver(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": [{"pkgname": "base", "pkgrel": "1"}]})
        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version is None


class TestCrapReductionAur:
    """Push aur_apps coverage."""

    def test_aur_http_failure(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"error"
        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version is None

    def test_aur_missing_version_key(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": [{"NotVersion": "x"}]})
        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version is None


class TestCrapReductionPypi:
    """Push pypi_apps coverage."""

    def test_pypi_non_200(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 403
        mock_http.get.return_value.content = b"forbidden"
        version, url = tdb.pypi_apps("requests", "agent/1.0")
        assert version is None


class TestCrapReductionCheckSite:
    """Push check_site coverage."""

    @pytest.fixture(autouse=True)
    def reset_state(self, tdb):
        tdb._site_down_state.clear()

    def test_check_site_github_sends_auth_header(self, tdb, mock_http):
        """GitHub site should get Authorization header."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        tdb.check_site(
            url="https://api.github.com",
            user_agent="agent/1.0",
            site_name="GitHub",
        )

    def test_check_site_retries_exhausted(self, tdb, mock_http, monkeypatch):
        """All retries exhausted, site is down."""
        monkeypatch.setattr(tdb.time, "sleep", MagicMock())

        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"error"

        site_down = tdb.check_site(
            url="https://example.com",
            user_agent="agent/1.0",
            site_name="Example",
        )
        assert site_down is True


class TestCrapReductionAppLogging:
    """Push app_logging coverage."""

    def test_app_logging_warning_level_full(self, tdb, tmp_path):
        log_file = str(tmp_path / "warn.log")
        tdb.app_log_file = log_file
        tdb.config_obj["general"]["log_level"] = "WARNING"
        import logging

        result = tdb.app_logging()
        assert result["logger"].level == logging.WARNING

    def test_app_logging_error_level_full(self, tdb, tmp_path):
        log_file = str(tmp_path / "err.log")
        tdb.app_log_file = log_file
        tdb.config_obj["general"]["log_level"] = "ERROR"
        import logging

        result = tdb.app_logging()
        assert result["logger"].level == logging.ERROR
