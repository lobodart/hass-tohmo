"""Helpers for Tohmo API values."""

from __future__ import annotations

from datetime import datetime, tzinfo

from homeassistant.util import dt as dt_util


def parse_datetime(value: str | None) -> datetime | None:
    """Parse an API timestamp."""
    return dt_util.parse_datetime(value) if value else None


def parse_session_time(value: str | None, tz: tzinfo) -> datetime | None:
    """Parse a session start/end time.

    Session times are local (French) times wrongly labelled as UTC: eco
    sessions start at "01:26:01Z" in summer and in winter alike, matching an
    eco window that starts at 01:26 local time.
    """
    parsed = parse_datetime(value)
    return parsed.replace(tzinfo=tz) if parsed else None
