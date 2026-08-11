"""Tests for pypi_apps, aor_apps, aur_apps, gitlab_apps."""

import json


class TestPypiApps:
    """pypi_apps(source_app_name, user_agent)."""

    def test_returns_version_from_json(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"info": {"version": "2.33.0"}})

        version, url = tdb.pypi_apps("requests", "agent/1.0")
        assert version == "2.33.0"
        assert "requests" in url

    def test_missing_info_key_caught(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({})

        version, url = tdb.pypi_apps("requests", "agent/1.0")
        assert version is None

    def test_missing_version_key_caught(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"info": {}})

        version, url = tdb.pypi_apps("requests", "agent/1.0")
        assert version is None

    def test_json_decode_failure(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"not json"

        version, url = tdb.pypi_apps("requests", "agent/1.0")
        assert version is None

    def test_http_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 404
        mock_http.get.return_value.content = b"not found"

        version, url = tdb.pypi_apps("requests", "agent/1.0")
        assert version is None
        assert url is not None


class TestAorApps:
    """aor_apps(source_app_name, user_agent)."""

    def test_returns_version_from_filtered_results(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            {
                "results": [
                    {"pkgname": "base", "pkgver": "3", "pkgrel": "1"},
                    {"pkgname": "base-devel", "pkgver": "1", "pkgrel": "2"},
                ]
            }
        )

        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version == "3-1"

    def test_exact_match_filters_fuzzy_results(self, tdb, mock_http):
        """Only exact pkgname match is returned."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            {
                "results": [
                    {"pkgname": "python", "pkgver": "3.12", "pkgrel": "1"},
                    {"pkgname": "python2", "pkgver": "2.7", "pkgrel": "1"},
                ]
            }
        )

        version, url = tdb.aor_apps("python", "agent/1.0")
        assert version == "3.12-1"

    def test_no_matching_package_raises_index_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps(
            {"results": [{"pkgname": "other", "pkgver": "1", "pkgrel": "1"}]}
        )

        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version is None

    def test_http_error_returns_none(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 500
        mock_http.get.return_value.content = b"error"

        version, url = tdb.aor_apps("base", "agent/1.0")
        assert version is None


class TestAurApps:
    """aur_apps(source_app_name, user_agent)."""

    def test_returns_version(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": [{"Version": "1.2.3-1"}]})

        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version == "1.2.3-1"
        assert "aur.archlinux.org" in url

    def test_empty_results_raises_index_error(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"results": []})

        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version is None

    def test_missing_results_key(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({})

        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version is None

    def test_json_decode_failure(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"not json"

        version, url = tdb.aur_apps("yay", "agent/1.0")
        assert version is None


class TestGitlabApps:
    """gitlab_apps(...)."""

    def test_branch_query_returns_commit_id(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({"id": "abc123"})

        version, url = tdb.gitlab_apps("myapp", "group/repo", "12345", "main", "branch", "agent/1.0")
        assert version == "abc123"
        assert "gitlab.com" in url

    def test_unknown_query_type_returns_none(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({})

        version, url = tdb.gitlab_apps("myapp", "group/repo", "12345", "main", "tag", "agent/1.0")
        assert version is None

    def test_missing_id_key(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = json.dumps({})

        version, url = tdb.gitlab_apps("myapp", "group/repo", "12345", "main", "branch", "agent/1.0")
        assert version is None

    def test_json_decode_failure(self, tdb, mock_http):
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"not json"

        version, url = tdb.gitlab_apps("myapp", "group/repo", "12345", "main", "branch", "agent/1.0")
        assert version is None
