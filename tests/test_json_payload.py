"""Tests for JSON payload handling in http_client.

Bug: github_create_release sends JSON via requests.post(data=json_string)
without a Content-Type: application/json header. GitHub's API rejects the
request with 422 Validation Failed ("tag_name is not a valid tag", etc).
The fix: http_client must support a json_payload parameter that uses the
requests json= argument, which automatically sets Content-Type to
application/json.
"""


class TestHttpClientJsonPayload:
    """http_client should send JSON with the correct Content-Type."""

    def test_post_with_json_payload_uses_json_kwarg(self, tdb, mock_http):
        """json_payload → requests called with json= (sets application/json)."""
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"ok"
        payload = {"tag_name": "1.2.4.5-1-01", "draft": False}

        return_code, status_code, content = tdb.http_client(
            url="https://api.github.com/repos/o/r/releases",
            user_agent="agent/1.0",
            request_type="post",
            json_payload=payload,
        )

        assert return_code == 0
        assert mock_http.post.call_args.kwargs.get("json") == payload
        # data= must NOT be used when json_payload is provided
        assert "data" not in mock_http.post.call_args.kwargs

    def test_post_without_json_payload_uses_data(self, tdb, mock_http):
        """Legacy data_payload still uses data= (no Content-Type change)."""
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"ok"

        return_code, status_code, content = tdb.http_client(
            url="https://api.github.com/repos/o/r/releases",
            user_agent="agent/1.0",
            request_type="post",
            data_payload='{"key":"value"}',
        )

        assert return_code == 0
        assert mock_http.post.call_args.kwargs.get("data") == '{"key":"value"}'
        assert "json" not in mock_http.post.call_args.kwargs

    def test_github_create_release_sends_json_payload(self, tdb, mock_http):
        """github_create_release must send json_payload (proper Content-Type)."""
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b'{"html_url":"https://x"}'

        return_code, status_code, content = tdb.github_create_release(
            "1:2.4.5-1", "main", "binhex", "arch-fdroidserver", "agent/1.0"
        )

        assert return_code == 0
        assert status_code == 201
        kwargs = mock_http.post.call_args.kwargs
        assert "json" in kwargs, "must use json= kwarg for Content-Type"
        sent = kwargs["json"]
        assert sent["tag_name"] == "1.2.4.5-1-01"
        assert sent["target_commitish"] == "main"
        assert sent["draft"] is False

    def test_github_create_release_sanitizes_colon_version(self, tdb, mock_http):
        """Colon in AUR version (epoch) replaced with dot in tag name."""
        mock_http.post.return_value.status_code = 201
        mock_http.post.return_value.content = b"ok"

        tdb.github_create_release("1:2.4.5-1", "main", "binhex", "arch-fdroidserver", "agent/1.0")

        sent = mock_http.post.call_args.kwargs["json"]
        assert sent["tag_name"] == "1.2.4.5-1-01"
