"""Tests for time_check — a pure function with no external dependencies."""

import datetime


class TestTimeCheck:
    """time_check(current_time, grace_period_mins, source_version_change_datetime)."""

    def test_time_delta_below_grace_period(self, tdb):
        """Returns False when elapsed time is less than the grace period."""
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        changed = datetime.datetime(2025, 1, 1, 11, 55, 0)  # 5 mins ago
        assert tdb.time_check(now, 10, changed) is False

    def test_time_delta_at_grace_period(self, tdb):
        """Returns True when elapsed time equals the grace period."""
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        changed = datetime.datetime(2025, 1, 1, 11, 50, 0)  # exactly 10 mins ago
        assert tdb.time_check(now, 10, changed) is True

    def test_time_delta_above_grace_period(self, tdb):
        """Returns True when elapsed time exceeds the grace period."""
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        changed = datetime.datetime(2025, 1, 1, 11, 40, 0)  # 20 mins ago
        assert tdb.time_check(now, 10, changed) is True

    def test_grace_period_zero(self, tdb):
        """With zero grace period, any elapsed time passes."""
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        changed = datetime.datetime(2025, 1, 1, 11, 59, 59)
        assert tdb.time_check(now, 0, changed) is True

    def test_zero_time_delta(self, tdb):
        """Zero delta should fail grace-period check."""
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        assert tdb.time_check(now, 10, now) is False

    def test_grace_period_string_to_int(self, tdb):
        """Grace period string is cast to int correctly."""
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        changed = datetime.datetime(2025, 1, 1, 11, 55, 0)
        assert tdb.time_check(now, "10", changed) is False

    def test_many_hours_elapsed(self, tdb):
        """Large time delta correctly passes."""
        now = datetime.datetime(2025, 1, 2, 12, 0, 0)
        changed = datetime.datetime(2025, 1, 1, 12, 0, 0)  # 24 hours ago
        assert tdb.time_check(now, 60, changed) is True

    def test_fractional_minutes(self, tdb):
        """Fractional minute handling."""
        now = datetime.datetime(2025, 1, 1, 12, 0, 30)
        changed = datetime.datetime(2025, 1, 1, 12, 0, 0)  # 30s ago
        # 30s = 0.5 min < 1 min grace
        assert tdb.time_check(now, 1, changed) is False
