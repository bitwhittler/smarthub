from datetime import UTC, datetime


from smarthub.models import ReadResolution
from smarthub.utils import align_datetime


def test_align_hourly() -> None:
    """Verify HOURLY snaps to 15-minute marks."""
    dt = datetime(2026, 4, 3, 14, 7, 22, tzinfo=UTC)

    # Snap down
    aligned_down = align_datetime(dt, ReadResolution.HOURLY, snap_up=False)
    assert aligned_down == datetime(2026, 4, 3, 14, 0, 0, tzinfo=UTC)

    # Snap up
    aligned_up = align_datetime(dt, ReadResolution.HOURLY, snap_up=True)
    assert aligned_up == datetime(2026, 4, 3, 14, 15, 0, tzinfo=UTC)

    # Edge cases
    dt_boundary = datetime(2026, 4, 3, 14, 15, 0, tzinfo=UTC)
    assert align_datetime(dt_boundary, ReadResolution.HOURLY, snap_up=False) == dt_boundary
    assert align_datetime(dt_boundary, ReadResolution.HOURLY, snap_up=True) == dt_boundary


def test_align_daily() -> None:
    """Verify DAILY snaps to midnight."""
    dt = datetime(2026, 4, 3, 14, 7, 22, tzinfo=UTC)

    # Snap down
    aligned_down = align_datetime(dt, ReadResolution.DAILY, snap_up=False)
    assert aligned_down == datetime(2026, 4, 3, 0, 0, 0, tzinfo=UTC)

    # Snap up
    aligned_up = align_datetime(dt, ReadResolution.DAILY, snap_up=True)
    assert aligned_up == datetime(2026, 4, 4, 0, 0, 0, tzinfo=UTC)


def test_align_monthly() -> None:
    """Verify MONTHLY snaps to 1st of month."""
    dt = datetime(2026, 4, 3, 14, 7, 22, tzinfo=UTC)

    # Snap down
    aligned_down = align_datetime(dt, ReadResolution.MONTHLY, snap_up=False)
    assert aligned_down == datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)

    # Snap up
    aligned_up = align_datetime(dt, ReadResolution.MONTHLY, snap_up=True)
    assert aligned_up == datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)

    # Rollover year
    dt_dec = datetime(2026, 12, 15, 12, 0, 0, tzinfo=UTC)
    aligned_up_dec = align_datetime(dt_dec, ReadResolution.MONTHLY, snap_up=True)
    assert aligned_up_dec == datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_align_daily_custom_tz() -> None:
    """Verify DAILY snaps to midnight in the specified timezone and converts back to UTC."""
    # 2026-04-03 22:00:00 local time in America/Denver (MDT is UTC-6)
    # The UTC equivalent is 2026-04-04 04:00:00 UTC
    dt_utc = datetime(2026, 4, 4, 4, 0, 0, tzinfo=UTC)

    # Align to DAILY in America/Denver: should snap to 2026-04-03 00:00:00 MDT
    # 2026-04-03 00:00:00 MDT is 2026-04-03 06:00:00 UTC
    aligned = align_datetime(dt_utc, ReadResolution.DAILY, tz="America/Denver")
    assert aligned == datetime(2026, 4, 3, 6, 0, 0, tzinfo=UTC)


def test_align_daily_dst_transition() -> None:
    """Verify DAILY snap_up logic correctly handles DST transitions without a 1-hour drift."""
    # March 8, 2026 is the DST spring-forward date in America/New_York (EST -> EDT)
    # 2026-03-08 00:30:00 EST is 2026-03-08 05:30:00 UTC
    dt_utc = datetime(2026, 3, 8, 5, 30, tzinfo=UTC)

    # Align DAILY with snap_up=True should land on March 9, 2026 00:00:00 EDT
    # March 9, 2026 00:00:00 EDT is 2026-03-09 04:00:00 UTC (not 05:00:00 UTC)
    aligned = align_datetime(dt_utc, ReadResolution.DAILY, snap_up=True, tz="America/New_York")
    assert aligned == datetime(2026, 3, 9, 4, 0, 0, tzinfo=UTC)
