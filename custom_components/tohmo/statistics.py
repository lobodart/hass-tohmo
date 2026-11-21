"""Hourly energy and cost statistics built from Tohmo charging sessions.

Tohmo only reports the energy of a session once it is over. Importing it as
external statistics places it on the hours it was actually consumed, and lets
the Energy dashboard show the whole history since the station was installed.

Each session is spread evenly over its duration (constant power, which is close
to reality for a 3.7/7.4 kW home charger). Costs only exist for sessions billed
per kWh: older sessions under a flat-rate plan have no cost and add nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta, tzinfo
import re
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN, LOGGER
from .util import parse_session_time

HOUR = timedelta(hours=1)


def statistic_ids(station: dict[str, Any]) -> tuple[str, str]:
    """Return the energy and cost statistic IDs of a station.

    Based on the station number printed on the charger (cpoStationId, e.g.
    FRABCP1234), falling back to the internal ID. Changing them would break
    the Energy dashboard configuration of existing users.
    """
    raw = station.get("cpoStationId") or station["id"]
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]", "_", str(raw).lower())).strip("_")
    return f"{DOMAIN}:energy_{slug}", f"{DOMAIN}:cost_{slug}"


def hourly_buckets(
    sessions: Iterable[dict[str, Any]], tz: tzinfo
) -> tuple[dict[datetime, float], dict[datetime, float]]:
    """Spread finished sessions over the UTC hours they overlap.

    Returns energy (kWh) and cost per hour start.
    """
    energy: dict[datetime, float] = defaultdict(float)
    cost: dict[datetime, float] = defaultdict(float)
    for session in sessions:
        start = parse_session_time(session.get("startedAt"), tz)
        end = parse_session_time(session.get("endedAt"), tz)
        kwh = session.get("kwhDelivered") or 0
        if start is None or end is None or kwh <= 0:
            continue  # In progress or empty: imported once finished.
        price = session.get("cost")
        start, end = dt_util.as_utc(start), dt_util.as_utc(end)
        if end <= start:
            end = start + timedelta(seconds=1)
        duration = (end - start).total_seconds()
        hour = start.replace(minute=0, second=0, microsecond=0)
        while hour < end:
            overlap = (min(end, hour + HOUR) - max(start, hour)).total_seconds()
            share = overlap / duration
            energy[hour] += kwh * share
            if price is not None:
                cost[hour] += price * share
            hour += HOUR
    return energy, cost


def _cumulative(buckets: dict[datetime, float]) -> list[StatisticData]:
    rows: list[StatisticData] = []
    total = 0.0
    for hour in sorted(buckets):
        total += buckets[hour]
        rows.append(StatisticData(start=hour, state=round(buckets[hour], 4), sum=round(total, 4)))
    return rows


def async_import_station_statistics(
    hass: HomeAssistant,
    station: dict[str, Any],
    name: str,
    sessions: Iterable[dict[str, Any]],
    currency: str,
    tz: tzinfo,
) -> None:
    """(Re)import the full hourly history of a station.

    Rows are rewritten in place, so importing again after a new or corrected
    session keeps the cumulative sums consistent.
    """
    energy_id, cost_id = statistic_ids(station)
    energy, cost = hourly_buckets(sessions, tz)
    if energy:
        async_add_external_statistics(
            hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"Tohmo {name} energy",
                source=DOMAIN,
                statistic_id=energy_id,
                unit_class=EnergyConverter.UNIT_CLASS,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
            _cumulative(energy),
        )
    if cost:
        async_add_external_statistics(
            hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"Tohmo {name} cost",
                source=DOMAIN,
                statistic_id=cost_id,
                unit_class=None,
                unit_of_measurement=currency,
            ),
            _cumulative(cost),
        )
    LOGGER.debug(
        "Imported %s hours of energy and %s hours of cost for %s",
        len(energy),
        len(cost),
        energy_id,
    )
