"""TDD tests for verify_ssl handling in http_client.

Bug: the daemon cannot connect to any HTTPS API in environments with SSL
inspection proxies (self-signed certs in the chain) because http_client
hardcodes verify_ssl=True with no escape hatch. Users must be able to
opt out via config while the secure default (True) is preserved.
"""

import pytest


class TestHttpClientVerifySsl:
    """http_client should honor the module-level verify_ssl setting."""

    @pytest.fixture(autouse=True)
    def reset_verify_ssl(self, tdb):
        """Reset the module-level verify_ssl default between tests."""
        tdb.verify_ssl = True
        yield
        tdb.verify_ssl = True

    def test_verify_ssl_true_passes_verify_true(self, tdb, mock_http):
        """Default/secure config passes verify=True to requests."""
        tdb.verify_ssl = True
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        return_code, status_code, content = tdb.http_client(
            url="https://api.github.com",
            user_agent="agent/1.0",
            request_type="get",
        )

        assert return_code == 0
        assert mock_http.get.call_args.kwargs.get("verify") is True

    def test_verify_ssl_false_passes_verify_false(self, tdb, mock_http):
        """SSL-inspection environments can opt out with verify_ssl=False."""
        tdb.verify_ssl = False
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        return_code, status_code, content = tdb.http_client(
            url="https://api.github.com",
            user_agent="agent/1.0",
            request_type="get",
        )

        assert return_code == 0
        assert mock_http.get.call_args.kwargs.get("verify") is False

    def test_verify_ssl_default_is_true(self, tdb, mock_http):
        """Without an explicit setting, verification stays enabled (secure default)."""
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.content = b"ok"

        tdb.http_client(
            url="https://api.github.com",
            user_agent="agent/1.0",
            request_type="get",
        )

        assert mock_http.get.call_args.kwargs.get("verify") is True

    def test_post_request_honors_verify_ssl_false(self, tdb, mock_http):
        """POST (release creation) also honors verify_ssl=False."""
        tdb.verify_ssl = False
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"created"

        tdb.http_client(
            url="https://api.github.com/repos/o/r/releases",
            user_agent="agent/1.0",
            request_type="post",
            data_payload="{}",
        )

        assert mock_http.post.call_args.kwargs.get("verify") is False
