"""Tests for the hourly statistics import."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tohmo.statistics import hourly_buckets, statistic_ids

from .conftest import STATION_ID

PARIS = ZoneInfo("Europe/Paris")


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=dt_util.UTC)


def test_statistic_ids() -> None:
    """IDs use the station number, or the internal ID when it is missing."""
    assert statistic_ids({"id": "x", "cpoStationId": "FR-ZPG P6550"}) == (
        "tohmo:energy_fr_zpg_p6550",
        "tohmo:cost_fr_zpg_p6550",
    )
    assert statistic_ids({"id": "a1b2c3d4-5e6f", "cpoStationId": None})[0] == (
        "tohmo:energy_a1b2c3d4_5e6f"
    )


def test_hourly_buckets_spread_sessions() -> None:
    """Energy and cost are spread evenly; times are Paris local times."""
    sessions = [
        # 01:30 -> 03:00 Paris (00:30 -> 02:00 UTC), 3 kWh, 0.51 EUR.
        {"startedAt": "2026-11-17T01:30:00.000Z", "endedAt": "2026-11-17T03:00:00.000Z",
         "kwhDelivered": 3.0, "cost": 0.51},
        # Flat-rate era: energy but no cost.
        {"startedAt": "2023-01-10T12:00:00.000Z", "endedAt": "2023-01-10T13:00:00.000Z",
         "kwhDelivered": 2.0, "cost": None},
        # In progress: ignored until finished.
        {"startedAt": "2026-11-20T01:26:00.000Z", "endedAt": None, "kwhDelivered": 5.0},
    ]
    energy, cost = hourly_buckets(sessions, PARIS)
    assert energy == {
        _utc(2023, 1, 10, 11): 2.0,
        _utc(2026, 11, 17, 0): 1.0,
        _utc(2026, 11, 17, 1): 2.0,
    }
    assert cost == {
        _utc(2026, 11, 17, 0): pytest.approx(0.17),
        _utc(2026, 11, 17, 1): pytest.approx(0.34),
    }


async def test_statistics_imported(
    hass: HomeAssistant,
    mock_tohmo: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Setting up the integration imports the session history as statistics."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    energy_id, cost_id = statistic_ids({"id": STATION_ID, "cpoStationId": "FRXXXP0001"})
    assert (energy_id, cost_id) == ("tohmo:energy_frxxxp0001", "tohmo:cost_frxxxp0001")
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utcnow() - timedelta(days=5 * 365),
        None,
        {energy_id, cost_id},
        "hour",
        None,
        {"sum"},
    )
    # Fixture: 10.5 kWh in 2022 (no cost), 24.32 kWh / 4.13 EUR in 2026.
    assert round(stats[energy_id][-1]["sum"], 2) == 34.82
    assert round(stats[cost_id][-1]["sum"], 2) == 4.13
    assert stats[energy_id][0]["start"] == _utc(2022, 11, 22, 18).timestamp()
