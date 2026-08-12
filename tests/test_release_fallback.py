"""Tests for GitHub release commitish fallback.

Bug: when the configured target_repo_branch does not exist on the target
repo (e.g. 'master' when the repo only has 'main'), GitHub returns a
misleading 422 Validation Failed that also claims "tag_name is not a valid
tag" even though the tag IS valid. The fix: retry the release creation once
with an empty target_commitish, which GitHub auto-maps to the repo's default
branch.
"""

from unittest.mock import MagicMock


class TestGithubReleaseCommitishFallback:
    """github_create_release should fall back to default branch on 422."""

    def test_retries_with_default_branch_on_422(self, tdb, mock_http):
        """422 (bad commitish) → retry with empty target_commitish → 201."""
        mock_http.post.side_effect = [
            MagicMock(
                status_code=422,
                content=b'{"message":"Validation Failed","errors":[{"code":"custom","field":"tag_name"}]}',
            ),
            MagicMock(status_code=201, content=b'{"html_url":"https://x"}'),
        ]

        return_code, status_code, content = tdb.github_create_release(
            "1:2.4.5-1", "master", "binhex", "arch-fdroidserver", "agent/1.0"
        )

        assert return_code == 0
        assert status_code == 201
        calls = mock_http.post.call_args_list
        assert len(calls) == 2
        # First attempt used the configured branch
        assert calls[0].kwargs["json"]["target_commitish"] == "master"
        # Fallback attempt uses empty string → GitHub default branch
        assert calls[1].kwargs["json"]["target_commitish"] == ""

    def test_no_retry_on_success(self, tdb, mock_http):
        """Successful first attempt → single request, no fallback."""
        mock_http.post.side_effect = [
            MagicMock(status_code=201, content=b'{"html_url":"https://x"}'),
        ]

        return_code, status_code, content = tdb.github_create_release(
            "1:2.4.5-1", "main", "binhex", "arch-fdroidserver", "agent/1.0"
        )

        assert return_code == 0
        assert status_code == 201
        assert mock_http.post.call_count == 1
        assert mock_http.post.call_args.kwargs["json"]["target_commitish"] == "main"

    def test_no_retry_on_non_422_error(self, tdb, mock_http):
        """Non-422 errors (e.g. 403, 500) do not trigger fallback."""
        mock_http.post.side_effect = [
            MagicMock(status_code=403, content=b'{"message":"Forbidden"}'),
        ]

        return_code, status_code, content = tdb.github_create_release(
            "1:2.4.5-1", "master", "binhex", "arch-fdroidserver", "agent/1.0"
        )

        assert return_code != 0
        assert mock_http.post.call_count == 1
