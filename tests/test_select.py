"""Tests for the charging mode selector."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID, ATTR_OPTION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tohmo.const import BASE_URL

from .conftest import STATE, STATION_ID, mock_api, mock_signin

ENTITY_ID = "select.domicile_charging_mode"
ACTION_URL = f"{BASE_URL}/api/stations/{STATION_ID}/actions/toggle-continuous"
OLD_ACTION = {"action": "eco", "status": "completed", "requestedAt": "2026-10-14T10:35:02Z"}


@pytest.fixture(autouse=True)
def fast_polling():
    """Do not wait between confirmation checks."""
    with patch("custom_components.tohmo.select.POLL_INTERVAL", 0):
        yield


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    state: dict[str, Any] | None = None,
) -> None:
    mock_signin(aioclient_mock)
    mock_api(
        aioclient_mock,
        {f"/api/stations/{STATION_ID}/state": {**STATE, "lastAction": OLD_ACTION, **(state or {})}},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _remock(
    aioclient_mock: AiohttpClientMocker, action: dict[str, Any] | int, state: dict[str, Any]
) -> None:
    """Answer the action, then serve the given station state."""
    aioclient_mock.clear_requests()
    mock_signin(aioclient_mock)
    if isinstance(action, int):
        aioclient_mock.post(ACTION_URL, status=action)
    else:
        aioclient_mock.post(ACTION_URL, json=action)
    mock_api(aioclient_mock, {f"/api/stations/{STATION_ID}/state": {**STATE, **state}})


async def _select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_OPTION: option},
        blocking=True,
    )


async def test_select_mode(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Selecting a mode posts the action and shows the new mode."""
    await _setup(hass, aioclient_mock, mock_config_entry)
    state = hass.states.get(ENTITY_ID)
    assert state.state == "eco"
    assert state.attributes["options"] == ["eco", "continuous"]

    _remock(
        aioclient_mock,
        {"command": "toggle-continuous", "status": "completed"},
        {"chargingMode": "continuous"},
    )
    await _select(hass, "continuous")
    await hass.async_block_till_done()

    posts = [call for call in aioclient_mock.mock_calls if call[0] == "POST" and "actions" in str(call[1])]
    assert len(posts) == 1
    assert hass.states.get(ENTITY_ID).state == "continuous"


async def test_select_waits_for_confirmation(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A pending action is confirmed through lastAction in the station state."""
    await _setup(hass, aioclient_mock, mock_config_entry)
    _remock(
        aioclient_mock,
        {"command": "toggle-continuous", "status": "pending"},
        {
            "chargingMode": "continuous",
            "lastAction": {"action": "continuous", "status": "completed", "requestedAt": "new"},
        },
    )
    await _select(hass, "continuous")
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "continuous"


async def test_select_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A refused or broken action is reported to the user."""
    await _setup(hass, aioclient_mock, mock_config_entry)

    _remock(
        aioclient_mock,
        {"command": "toggle-continuous", "status": "pending"},
        {"lastAction": {"action": "continuous", "status": "failed", "requestedAt": "new"}},
    )
    with pytest.raises(HomeAssistantError, match="refused"):
        await _select(hass, "continuous")

    _remock(aioclient_mock, 409, {})
    with pytest.raises(HomeAssistantError, match="HTTP 409"):
        await _select(hass, "continuous")
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "eco"


async def test_select_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Without the actions, the selector is unavailable and cannot be used."""
    await _setup(hass, aioclient_mock, mock_config_entry, {"availableActions": ["toggle-eco"]})
    assert hass.states.get(ENTITY_ID).state == "eco"
    with pytest.raises(ServiceValidationError):
        await _select(hass, "continuous")

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    aioclient_mock.clear_requests()
    await _setup(hass, aioclient_mock, mock_config_entry, {"availableActions": []})
    assert hass.states.get(ENTITY_ID).state == "unavailable"
