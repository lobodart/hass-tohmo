"""Eco schedule (off-peak hours) computations.

The station state lists the eco-mode windows as local "HH:MM" pairs, e.g.
[{"from": "01:26", "to": "07:56"}, {"from": "12:26", "to": "13:56"}].
A window whose end is before its start spans midnight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from typing import Any


@dataclass(frozen=True)
class EcoWindow:
    """One concrete eco window."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class EcoStatus:
    """Eco schedule status at a given instant."""

    active: bool
    next_start: datetime | None
    # Next instant at which `active` or `next_start` changes.
    next_change: datetime | None


def parse_time(value: Any) -> time | None:
    """Parse "HH:MM"."""
    try:
        hours, minutes = str(value).split(":")[:2]
        return time(int(hours), int(minutes))
    except (TypeError, ValueError):
        return None


def eco_windows(
    schedules: list[dict[str, Any]], now: datetime, tz: tzinfo
) -> list[EcoWindow]:
    """Return concrete windows from yesterday to tomorrow, sorted by start."""
    today = now.astimezone(tz).date()
    windows: list[EcoWindow] = []
    for slot in schedules:
        start_time = parse_time(slot.get("from"))
        end_time = parse_time(slot.get("to"))
        if start_time is None or end_time is None or start_time == end_time:
            continue
        for offset in (-1, 0, 1):
            day = today + timedelta(days=offset)
            start = datetime.combine(day, start_time, tz)
            end_day = day if end_time > start_time else day + timedelta(days=1)
            end = datetime.combine(end_day, end_time, tz)
            windows.append(EcoWindow(start, end))
    return sorted(windows, key=lambda window: window.start)


def eco_status(
    schedules: list[dict[str, Any]] | None, now: datetime, tz: tzinfo
) -> EcoStatus:
    """Return whether eco hours are in progress and when the next ones start."""
    windows = eco_windows(schedules or [], now, tz)
    active = any(window.start <= now < window.end for window in windows)
    next_start = min((w.start for w in windows if w.start > now), default=None)
    next_change = min(
        (t for w in windows for t in (w.start, w.end) if t > now), default=None
    )
    return EcoStatus(active, next_start, next_change)
