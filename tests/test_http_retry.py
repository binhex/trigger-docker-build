"""Tests for transient HTTP error retry in http_client.

Bug: a single 5xx response (e.g. 502 Bad Gateway when archlinux.org is under
DDoS load) is treated as an immediate terminal failure, triggering an email
notification and skipping the app. Transient server errors should be retried
a bounded number of times with a short delay before falling through to the
normal failure handling. Client errors (4xx) must NOT be retried.
"""

from unittest.mock import MagicMock


class TestHttpClientTransientRetry:
    """http_client should retry transient 5xx errors."""

    def test_retries_transient_502_then_succeeds(self, tdb, mock_http):
        """A 502 followed by a 200 should succeed after retrying."""
        mock_http.get.side_effect = [
            MagicMock(status_code=502, content=b"502 bad gateway"),
            MagicMock(status_code=200, content=b'{"version":"2.9.0-2"}'),
        ]

        return_code, status_code, content = tdb.http_client(
            url="https://archlinux.org/packages/search/json/?q=jellyfin",
            user_agent="agent/1.0",
            request_type="get",
        )

        assert return_code == 0
        assert status_code == 200
        assert mock_http.get.call_count == 2

    def test_gives_up_after_bounded_retries(self, tdb, mock_http):
        """Persistent 502 retries a bounded number of times, then fails."""
        mock_http.get.side_effect = [
            MagicMock(status_code=502, content=b"502 bad gateway"),
            MagicMock(status_code=502, content=b"502 bad gateway"),
            MagicMock(status_code=502, content=b"502 bad gateway"),
            MagicMock(status_code=502, content=b"502 bad gateway"),
        ]

        return_code, status_code, content = tdb.http_client(
            url="https://archlinux.org/packages/search/json/?q=jellyfin",
            user_agent="agent/1.0",
            request_type="get",
        )

        # Fails after retries are exhausted (never more than 3 attempts)
        assert return_code != 0
        assert mock_http.get.call_count <= 3

    def test_does_not_retry_client_error_404(self, tdb, mock_http):
        """404 (client error) fails immediately with no retry."""
        mock_http.get.side_effect = [
            MagicMock(status_code=404, content=b"not found"),
        ]

        return_code, status_code, content = tdb.http_client(
            url="https://archlinux.org/packages/search/json/?q=missing",
            user_agent="agent/1.0",
            request_type="get",
        )

        assert return_code != 0
        assert mock_http.get.call_count == 1

    def test_retries_503_and_504_then_succeeds(self, tdb, mock_http):
        """503 and 504 are also transient and retried until success."""
        mock_http.get.side_effect = [
            MagicMock(status_code=503, content=b"service unavailable"),
            MagicMock(status_code=504, content=b"gateway timeout"),
            MagicMock(status_code=200, content=b'{"version":"2.9.0-2"}'),
        ]

        return_code, status_code, content = tdb.http_client(
            url="https://archlinux.org/packages/search/json/?q=jellyfin",
            user_agent="agent/1.0",
            request_type="get",
        )

        assert return_code == 0
        assert status_code == 200
        assert mock_http.get.call_count == 3
