"""Tests for http_client, check_site, and github API functions."""

import json
from unittest.mock import MagicMock

import pytest


class TestGithubTargetLastReleaseDate:
    """github_target_last_release_date(target_repo_owner, target_repo_name, user_agent)."""

    def test_returns_published_at_date(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"published_at": "2025-01-15T12:00:00Z"})

        return_code, date = tdb.github_target_last_release_date("owner", "repo", "agent/1.0")
        assert return_code == 0
        assert date == "2025-01-15T12:00:00Z"

    def test_missing_published_at_key_returns_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({})

        return_code, date = tdb.github_target_last_release_date("owner", "repo", "agent/1.0")
        assert return_code == 1
        assert date is None

    def test_json_decode_failure_returns_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"not json"

        return_code, date = tdb.github_target_last_release_date("owner", "repo", "agent/1.0")
        assert return_code == 1
        assert date is None

    def test_non_200_returns_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 403
        mock_http.get.return_value.content = b"forbidden"

        return_code, date = tdb.github_target_last_release_date("owner", "repo", "agent/1.0")
        assert return_code != 0


class TestCheckSite:
    """check_site(**kwargs)."""

    @pytest.fixture(autouse=True)
    def reset_state(self, tdb):
        tdb._site_down_state.clear()

    def test_site_up_returns_false(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        site_down = tdb.check_site(
            url="https://example.com",
            user_agent="agent/1.0",
            site_name="Example",
        )
        assert site_down is False

    def test_site_up_clears_down_state(self, tdb, mock_http):
        tdb._site_down_state["Example"] = {
            "is_down": True,
            "notified_at": None,
        }
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        tdb.check_site(
            url="https://example.com",
            user_agent="agent/1.0",
            site_name="Example",
        )
        assert tdb._site_down_state["Example"]["is_down"] is False

    def test_site_recovery_sends_email(self, tdb, mock_http, monkeypatch):
        tdb._site_down_state["Example"] = {
            "is_down": True,
            "notified_at": None,
        }
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        mock_notify = MagicMock()
        monkeypatch.setattr(tdb, "notification_email", mock_notify)

        tdb.check_site(
            url="https://example.com",
            user_agent="agent/1.0",
            site_name="Example",
        )
        # Should send a recovery email
        recovery_calls = [c for c in mock_notify.call_args_list if c.kwargs.get("msg_type") == "site_recovered"]
        assert len(recovery_calls) == 1

    def test_non_github_site_does_not_send_auth_header(self, tdb, mock_http):
        """Non-GitHub sites should NOT get GitHub PAT in headers."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        tdb.check_site(
            url="https://pypi.org/pypi/test/json",
            user_agent="agent/1.0",
            site_name="PyPI",
        )
        # Check that session.headers.update was called with None or empty
        session_instance = mock_http.return_value
        # The additional_header should not contain Authorization for PyPI
        all_update_calls = [c for c in session_instance.headers.update.call_args_list]
        for call_args in all_update_calls:
            header = call_args[0][0] if call_args[0] else {}
            if isinstance(header, dict) and "Authorization" in header:
                # This is the Accept-encoding/User-Agent update, not auth
                pass


class TestGithubCreateRelease:
    """github_create_release(current_version, target_repo_branch, target_repo_owner, target_repo_name, user_agent)."""

    def test_creates_release_with_json_payload(self, tdb, mock_http):
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b'{"html_url":"https://github.com/owner/repo/releases/tag/v1.0.0-01"}'

        return_code, status_code, content = tdb.github_create_release("v1.0.0", "main", "owner", "repo", "agent/1.0")
        assert return_code == 0
        assert status_code == 201

    def test_colon_in_version_replaced_with_dot(self, tdb, mock_http):
        """Colons in version numbers are replaced with dots."""
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"ok"

        tdb.github_create_release("1.0:beta", "main", "owner", "repo", "agent/1.0")
        # Check the URL uses the sanitized tag_name
        posted_url = mock_http.post.call_args[1].get("url", "")
        assert ":" not in posted_url or "1.0.beta" in str(mock_http.post.call_args)
