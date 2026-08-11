"""Tests for github_apps — JSON parsing of GitHub API responses."""

import json


class TestGithubApps:
    def test_tag_query_type_returns_first_tag_name(self, tdb, mock_http):
        """Tag query returns content[0]['name']."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            [
                {"name": "v2.0.0"},
                {"name": "v1.0.0"},
            ]
        )

        version, url = tdb.github_apps("myapp", "tag", "owner/repo", "agent/1.0", None)

        assert version == "v2.0.0"
        assert "owner/repo/myapp/tags" in url

    def test_release_query_type_returns_latest_tag_name(self, tdb, mock_http):
        """Release (latest) query returns content['tag_name']."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            {
                "tag_name": "v3.0.0",
            }
        )

        version, url = tdb.github_apps("myapp", "release", "owner/repo", "agent/1.0", None)

        assert version == "v3.0.0"
        assert "releases/latest" in url

    def test_pre_release_query_type(self, tdb, mock_http):
        """Pre-release query returns content[0]['tag_name']."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            [
                {"tag_name": "v4.0.0-rc1"},
                {"tag_name": "v3.0.0"},
            ]
        )

        version, url = tdb.github_apps("myapp", "pre-release", "owner/repo", "agent/1.0", None)

        assert version == "v4.0.0-rc1"
        assert "releases" in url

    def test_branch_query_type_with_branch_name(self, tdb, mock_http):
        """Branch query with source_branch_name appends ?sha= parameter."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            [
                {"sha": "abc123def456"},
            ]
        )

        version, url = tdb.github_apps("myapp", "branch", "owner/repo", "agent/1.0", "develop")

        assert version == "abc123def456"
        assert "develop" in url
        assert "commits" in url

    def test_unknown_query_type_returns_none(self, tdb, mock_http):
        """Unknown query type warns and returns None."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps([{}])

        version, url = tdb.github_apps("myapp", "unknown", "owner/repo", "agent/1.0", None)

        assert version is None

    def test_json_decode_failure_returns_none(self, tdb, mock_http):
        """Invalid JSON returns None."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"not json"

        version, url = tdb.github_apps("myapp", "release", "owner/repo", "agent/1.0", None)

        assert version is None

    def test_missing_key_in_json_caught(self, tdb, mock_http):
        """Missing expected key caught by KeyError and returns None."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({})

        version, url = tdb.github_apps("myapp", "release", "owner/repo", "agent/1.0", None)

        assert version is None

    def test_http_error_returns_none(self, tdb, mock_http):
        """HTTP error returns None and source_site_url."""
        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"server error"

        version, url = tdb.github_apps("myapp", "release", "owner/repo", "agent/1.0", None)

        assert version is None
        assert url is not None  # source_site_url is still returned
