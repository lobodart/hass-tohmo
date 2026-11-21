"""Tests for off-peak (eco schedule) entities."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tohmo.eco import eco_status

from .conftest import STATE, STATION_ID, mock_api, mock_signin

PARIS = ZoneInfo("Europe/Paris")
SCHEDULES = [{"from": "01:26", "to": "07:56"}, {"from": "12:26", "to": "13:56"}]


def _paris(*args: int) -> datetime:
    return datetime(*args, tzinfo=PARIS)


def test_eco_status() -> None:
    """Windows are local times; the next start skips the window in progress."""
    status = eco_status(SCHEDULES, _paris(2026, 11, 20, 10, 0), PARIS)
    assert not status.active
    assert status.next_start == _paris(2026, 11, 20, 12, 26)
    assert status.next_change == _paris(2026, 11, 20, 12, 26)

    status = eco_status(SCHEDULES, _paris(2026, 11, 20, 12, 26), PARIS)
    assert status.active
    assert status.next_start == _paris(2026, 11, 21, 1, 26)
    assert status.next_change == _paris(2026, 11, 20, 13, 56)


def test_eco_status_over_midnight() -> None:
    """A window ending before it starts spans midnight."""
    schedules = [{"from": "22:00", "to": "06:00"}]
    status = eco_status(schedules, _paris(2026, 11, 20, 2, 0), PARIS)
    assert status.active
    assert status.next_change == _paris(2026, 11, 20, 6, 0)
    assert status.next_start == _paris(2026, 11, 20, 22, 0)
    assert eco_status(schedules, _paris(2026, 11, 20, 23, 0), PARIS).active
    assert not eco_status(schedules, _paris(2026, 11, 20, 12, 0), PARIS).active


def test_eco_status_invalid() -> None:
    """Missing or malformed schedules give no window."""
    for schedules in (None, [], [{"from": "x", "to": None}]):
        status = eco_status(schedules, _paris(2026, 11, 20, 2, 0), PARIS)
        assert not status.active
        assert status.next_start is None
        assert status.next_change is None


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_off_peak_entities_follow_the_clock(
    hass: HomeAssistant,
    mock_tohmo: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The off-peak sensor and the next start switch at the exact boundaries."""
    freezer.move_to(_paris(2026, 11, 20, 12, 0))
    await _setup(hass, mock_config_entry)

    off_peak = "binary_sensor.domicile_off_peak_hours"
    next_off_peak = "sensor.domicile_next_off_peak"
    assert hass.states.get(off_peak).state == "off"
    assert hass.states.get(next_off_peak).state == "2026-11-20T11:26:00+00:00"

    freezer.move_to(_paris(2026, 11, 20, 12, 26))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(off_peak).state == "on"
    assert hass.states.get(next_off_peak).state == "2026-11-21T00:26:00+00:00"

    freezer.move_to(_paris(2026, 11, 20, 13, 56))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(off_peak).state == "off"
    assert hass.states.get(next_off_peak).state == "2026-11-21T00:26:00+00:00"


async def test_slot_sensors_follow_the_schedule(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """One start/end pair per window, added or made unavailable as Tohmo changes."""
    mock_signin(aioclient_mock)
    mock_api(aioclient_mock)
    await _setup(hass, mock_config_entry)

    assert hass.states.get("sensor.domicile_off_peak_1_start").state == "01:26"
    assert hass.states.get("sensor.domicile_off_peak_1_end").state == "07:56"
    assert hass.states.get("sensor.domicile_off_peak_2_start").state == "12:26"
    assert hass.states.get("sensor.domicile_off_peak_2_end").state == "13:56"
    assert hass.states.get("sensor.domicile_off_peak_3_start") is None

    async def refresh_with(schedules: list[dict[str, str]]) -> None:
        aioclient_mock.clear_requests()
        mock_signin(aioclient_mock)
        mock_api(
            aioclient_mock,
            {f"/api/stations/{STATION_ID}/state": {**STATE, "ecoSchedules": schedules}},
        )
        await mock_config_entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    # Tohmo adds a third window: new entities appear without a restart.
    await refresh_with([*SCHEDULES, {"from": "20:00", "to": "21:00"}])
    assert hass.states.get("sensor.domicile_off_peak_3_start").state == "20:00"
    assert hass.states.get("sensor.domicile_off_peak_3_end").state == "21:00"

    # It is removed again: its entities become unavailable, the others stay.
    await refresh_with(SCHEDULES)
    assert hass.states.get("sensor.domicile_off_peak_3_start").state == "unavailable"
    assert hass.states.get("sensor.domicile_off_peak_1_start").state == "01:26"
