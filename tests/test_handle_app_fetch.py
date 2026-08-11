"""Tests for _handle_app_fetch — the central failure-counter helper."""

from unittest.mock import MagicMock

import pytest


class TestHandleAppFetch:
    """Handle the common 'fetch succeeded or failed' pattern for a single app.

    On failure: increments the persistent counter, sends an app_error email if
    the count is at or below APP_DOWN_COUNTER_MAX, or suppresses notification.
    On success: resets this app's failure counter.
    """

    @pytest.fixture(autouse=True)
    def setup_counters(self, tdb):
        """Reset counters before each test."""
        tdb._app_down_counters.clear()
        tdb.APP_DOWN_COUNTER_MAX = 3

    def test_success_resets_counter_and_returns_true(self, tdb):
        tdb._app_down_counters["github:myapp"] = 5
        result = tdb._handle_app_fetch("v1.0.0", "github:myapp", "GitHub", "myapp", "owner/repo", "https://example.com")
        assert result is True
        assert "github:myapp" not in tdb._app_down_counters

    def test_failure_increments_counter_and_returns_false(self, tdb):
        result = tdb._handle_app_fetch(None, "github:myapp", "GitHub", "myapp", "owner/repo", "https://example.com")
        assert result is False
        assert tdb._app_down_counters["github:myapp"] == 1

    def test_counter_at_max_suppresses_email(self, tdb, monkeypatch):
        """At APP_DOWN_COUNTER_MAX, notification is suppressed."""
        tdb._app_down_counters["github:myapp"] = 3

        # Capture notification_email calls
        mock_notify = MagicMock()
        monkeypatch.setattr(tdb, "notification_email", mock_notify)

        result = tdb._handle_app_fetch(None, "github:myapp", "GitHub", "myapp", "owner/repo", "https://example.com")
        assert result is False
        mock_notify.assert_not_called()
        assert tdb._app_down_counters["github:myapp"] == 4

    def test_counter_below_max_sends_email(self, tdb, monkeypatch):
        """Below max, notification is sent."""
        tdb._app_down_counters["github:myapp"] = 1

        mock_notify = MagicMock()
        monkeypatch.setattr(tdb, "notification_email", mock_notify)

        result = tdb._handle_app_fetch(None, "github:myapp", "GitHub", "myapp", "owner/repo", "https://example.com")
        assert result is False
        mock_notify.assert_called_once()
        assert tdb._app_down_counters["github:myapp"] == 2

    def test_multiple_failures_increment(self, tdb):
        """Each failure increments the counter."""
        for i in range(5):
            result = tdb._handle_app_fetch(None, "github:myapp", "GitHub", "myapp", "owner/repo", "https://example.com")
            assert result is False
        assert tdb._app_down_counters["github:myapp"] == 5

    def test_site_keys_are_independent(self, tdb):
        """Different site:app keys don't interfere."""
        result_a = tdb._handle_app_fetch(None, "github:app-a", "GitHub", "app-a", "owner/repo", "https://a.com")
        assert result_a is False
        assert tdb._app_down_counters["github:app-a"] == 1
        assert "github:app-b" not in tdb._app_down_counters
