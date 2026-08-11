"""Targeted tests for remaining uncovered branches in TriggerDockerBuild.py.

Coverage gaps after previous passes: http_client branches, error return paths
in app-query functions, monitor_sites edge cases.
"""

import json
from unittest.mock import MagicMock

import pytest


class TestHttpClientRemaining:
    """Cover remaining http_client() code paths."""

    def test_additional_header_empty_dict_skipped(self, tdb, mock_http):
        """Empty dict additional_header is skipped safely."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            user_agent="agent/1.0",
            request_type="get",
            additional_header={},
        )
        assert return_code == 0

    def test_http_client_without_user_agent_kwarg(self, tdb, mock_http):
        """No URL kwarg → early return with error."""
        return_code, status_code, content = tdb.http_client(
            url="https://example.com",
            request_type="get",
        )
        assert return_code == 1

    def test_no_kwargs_returns_error(self, tdb):
        """Empty kwargs returns error."""
        return_code, status_code, content = tdb.http_client()
        assert return_code == 1


class TestGitlabAppsErrorPaths:
    """Cover remaining gitlab_apps() branches."""

    def test_missing_id_in_response(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({})

        version, url = tdb.gitlab_apps("app", "group/repo", "123", "main", "branch", "agent/1.0")
        assert version is None

    def test_error_with_invalid_json(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"<html>error</html>"

        version, url = tdb.gitlab_apps("app", "group/repo", "123", "main", "branch", "agent/1.0")
        assert version is None


class TestAorAppsErrorPaths:
    """Cover remaining aor_apps() branches."""

    def test_empty_results_list(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": []})

        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version is None

    def test_invalid_json_response(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"not valid json"

        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version is None

    def test_missing_mandatory_keys_in_result(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            {
                "results": [{"pkgname": "base"}]  # no pkgver/pkgrel
            }
        )

        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version is None


class TestAurAppsErrorPaths:
    """Cover remaining aur_apps() branches."""

    def test_non_200_response(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 404
        mock_http.get.return_value.content = b"not found"

        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version is None

    def test_missing_version_field(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": [{"OtherField": "1.0"}]})

        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version is None


class TestMonitorSitesDeepCoverage:
    """Cover deep monitor_sites() branches missed by previous tests."""

    @pytest.fixture(autouse=True)
    def reset_state(self, tdb):
        tdb._app_down_counters.clear()
        tdb._site_down_state.clear()

    def test_pypi_site_down_skips(self, tdb, mock_http, monkeypatch):
        """When PyPI is marked down, pypi apps are skipped."""
        # Return True (down) for the PyPI site check
        mock_check_site = MagicMock()
        # First 5 calls = site checks; PyPI is 3rd call
        # GitHub=False, GitLab=False, PyPI=True, AOR=False, AUR=False
        mock_check_site.side_effect = [False, False, True, False, False]
        monkeypatch.setattr(tdb, "check_site", mock_check_site)

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

    def test_aor_site_down_skips(self, tdb, mock_http, monkeypatch):
        """When AOR is marked down, aor apps are skipped."""
        mock_check_site = MagicMock(side_effect=[False, False, False, True, False])
        monkeypatch.setattr(tdb, "check_site", mock_check_site)

        tdb.config_obj["monitor_sites"]["site_list"] = [
            {
                "source_site_name": "aor",
                "source_app_name": "base",
                "source_repo_name": "archlinux",
                "target_repo_name": "docker-image",
                "action": "notify",
            }
        ]

        tdb.monitor_sites()

    def test_aur_site_down_skips(self, tdb, mock_http, monkeypatch):
        """When AUR is marked down, aur apps are skipped."""
        mock_check_site = MagicMock(side_effect=[False, False, False, False, True])
        monkeypatch.setattr(tdb, "check_site", mock_check_site)

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

    def test_aor_app_fetch_fails_increments_counter(self, tdb, mock_http, monkeypatch):
        """AOR app fetch failure increments the persistent counter."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        # Make aor_apps return None (HTTP error)
        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"error"

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
        assert tdb._app_down_counters.get("aor:base", 0) == 1

    def test_aur_app_fetch_fails_increments_counter(self, tdb, mock_http, monkeypatch):
        """AUR app fetch failure increments the persistent counter."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"error"

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
        assert tdb._app_down_counters.get("aur:yay", 0) == 1

    def test_pypi_app_fetch_fails_increments_counter(self, tdb, mock_http, monkeypatch):
        """PyPI app fetch failure increments the persistent counter."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"error"

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
        assert tdb._app_down_counters.get("pypi:requests", 0) == 1

    def test_grace_period_none_triggers(self, tdb, mock_http, monkeypatch):
        """When grace_period_mins is None, trigger proceeds immediately."""
        monkeypatch.setattr(tdb, "check_site", MagicMock(return_value=False))

        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"tag_name": "v2.0.0"})
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b'{"html_url":"https://..."}'

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

        tdb.monitor_sites()
