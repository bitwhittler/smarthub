"""Utility functions for date and time alignment."""

from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from .models import ReadResolution


def align_datetime(
    dt: datetime,
    resolution: ReadResolution,
    snap_up: bool = False,
    tz: str | tzinfo = UTC,
) -> datetime:
    """Align a datetime to the nearest interval boundary based on resolution and timezone.

    Args:
        dt: The datetime to align.
        resolution: The resolution to align to (HOURLY, DAILY, MONTHLY).
        snap_up: If True, rounds up to the next interval boundary.
        tz: The target timezone for alignment (string or tzinfo).

    Returns:
        The aligned datetime in UTC.

    """
    # Resolve tzinfo
    tz_info = ZoneInfo(tz) if isinstance(tz, str) else tz

    # Ensure timezone-aware in the target timezone
    dt = dt.replace(tzinfo=tz_info) if dt.tzinfo is None else dt.astimezone(tz_info)

    if resolution == ReadResolution.HOURLY:
        # User confirmed HOURLY is 15-minute chunks
        minute = (dt.minute // 15) * 15
        aligned = dt.replace(minute=minute, second=0, microsecond=0)
        if snap_up and aligned < dt:
            aligned += timedelta(minutes=15)
        return aligned.astimezone(UTC)

    if resolution == ReadResolution.DAILY:
        aligned = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if snap_up and aligned < dt:
            # Strip timezone to perform naive calendar day math, then re-apply to avoid DST shifts
            naive = aligned.replace(tzinfo=None) + timedelta(days=1)
            aligned = naive.replace(tzinfo=tz_info)
        return aligned.astimezone(UTC)

    if resolution == ReadResolution.MONTHLY:
        aligned = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if snap_up and aligned < dt:
            # Manually handle month rollover
            if aligned.month == 12:
                aligned = aligned.replace(year=aligned.year + 1, month=1)
            else:
                aligned = aligned.replace(month=aligned.month + 1)
        return aligned.astimezone(UTC)

    return dt.astimezone(UTC)
