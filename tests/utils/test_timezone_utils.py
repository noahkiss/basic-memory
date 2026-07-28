"""Tests for timezone utilities."""

from datetime import datetime, timezone


from basic_memory.utils import ensure_timezone_aware


class TestEnsureTimezoneAware:
    """Tests for ensure_timezone_aware function."""

    def test_already_timezone_aware_returns_unchanged(self):
        """Timezone-aware datetime should be returned unchanged."""
        dt = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        result = ensure_timezone_aware(dt)
        assert result == dt
        assert result.tzinfo == timezone.utc

    def test_naive_datetime_interpreted_as_local(self):
        """SQLite stores no offset, so naive datetimes are local time."""
        naive_dt = datetime(2024, 1, 15, 12, 30, 0)
        result = ensure_timezone_aware(naive_dt)

        # Should have some timezone info (local)
        assert result.tzinfo is not None
        # The datetime should be converted to local timezone
        # We can't assert exact timezone as it depends on system

    def test_naive_datetime_does_not_shift_wall_clock_time(self):
        """Tagging a naive value with the local offset must not move the clock."""
        naive_dt = datetime(2024, 6, 15, 18, 0, 0)  # Summer time
        result = ensure_timezone_aware(naive_dt)

        assert result.hour == 18
        assert result.replace(tzinfo=None) == naive_dt
