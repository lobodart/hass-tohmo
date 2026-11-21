"""Tests for Tohmo setup, sensors and session handling."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.tohmo.api import DEFAULT_SIGNIN_ACTION_ID, SESSION_COOKIE, TohmoClient
from custom_components.tohmo.const import BASE_URL

from .conftest import ME, SIGNIN_OK_BODY, mock_api, mock_signin


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensors(
    hass: HomeAssistant,
    mock_tohmo: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sensors expose totals, last session, live state and billing period."""
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    total = hass.states.get("sensor.domicile_total_energy")
    assert total is not None
    assert float(total.state) == 34.82
    assert total.attributes["state_class"] == "total_increasing"
    assert total.attributes["unit_of_measurement"] == "kWh"
    assert total.attributes["session_count"] == 2

    cost = hass.states.get("sensor.domicile_total_cost")
    assert float(cost.state) == 4.13
    assert cost.attributes["unit_of_measurement"] == "EUR"

    assert float(hass.states.get("sensor.domicile_last_session_energy").state) == 24.32
    # Session times are Paris local times labelled as UTC: 01:26 Paris = 00:26 UTC.
    assert hass.states.get("sensor.domicile_last_session_start").state == (
        "2026-11-17T00:26:01+00:00"
    )
    assert hass.states.get("sensor.domicile_last_session_duration").state == "389"

    mode = hass.states.get("sensor.domicile_charging_mode")
    assert mode.state == "eco"
    assert mode.attributes["eco_schedules"] == ["01:26-07:56", "12:26-13:56"]

    assert hass.states.get("binary_sensor.domicile_charging").state == "off"

    assert float(hass.states.get("sensor.tohmo_account_billing_period_energy").state) == 54.72
    assert float(hass.states.get("sensor.tohmo_account_billing_period_cost").state) == 9.29
    assert hass.states.get("sensor.tohmo_account_billing_period_sessions").state == "3"

    fee = hass.states.get("sensor.domicile_monthly_subscription")
    assert float(fee.state) == 12.9
    assert fee.attributes["unit_of_measurement"] == "EUR"
    assert fee.attributes["start_date"] == "2022-11-21"
    peak = hass.states.get("sensor.domicile_peak_price")
    assert float(peak.state) == 0.28
    assert peak.attributes["unit_of_measurement"] == "EUR/kWh"
    assert float(hass.states.get("sensor.domicile_off_peak_price").state) == 0.17

    invoice = hass.states.get("sensor.tohmo_account_last_invoice")
    assert float(invoice.state) == 35.62
    assert invoice.attributes["issued_at"] == "2026-11-04"
    assert invoice.attributes["status"] == "pending"
    pending = hass.states.get("sensor.tohmo_account_pending_invoices")
    assert pending.state == "2"
    assert pending.attributes["total_amount"] == 68.63


async def test_secondary_data_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Missing preferences, contracts or invoices do not break the main sensors."""
    mock_signin(aioclient_mock)
    mock_api(
        aioclient_mock,
        {"/api/me/preferences": 500, "/api/contracts": 500, "/api/invoices": 500},
    )
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    # Without preferences, the device falls back to the station label.
    assert float(hass.states.get("sensor.borne_garage_total_energy").state) == 34.82
    assert hass.states.get("sensor.borne_garage_peak_price") is None
    assert hass.states.get("sensor.tohmo_account_last_invoice") is None


async def test_invalid_credentials_start_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Rejected credentials put the entry in reauth instead of retrying forever."""
    mock_signin(aioclient_mock, ok=False)
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_expired_session_signs_in_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 triggers a new sign-in and the request is retried with the new token."""
    tokens = iter(["token-1", "token-2"])
    calls: list[str | None] = []

    async def signin(method, url, data):
        return AiohttpClientMockResponse(
            method,
            url,
            text=SIGNIN_OK_BODY,
            headers={"x-action-redirect": "/stations;push"},
            cookies={SESSION_COOKIE: next(tokens)},
        )

    async def me(method, url, data):
        # The mocker does not expose request headers to side effects, so the
        # first call answers 401 and the next ones succeed.
        calls.append(url)
        return AiohttpClientMockResponse(method, url, status=401 if len(calls) == 1 else 200, json=ME)

    aioclient_mock.post(f"{BASE_URL}/sign-in", side_effect=signin)
    aioclient_mock.get(f"{BASE_URL}/api/me", side_effect=me)

    client = TohmoClient(async_get_clientsession(hass), "me@example.com", "secret")
    assert await client.get_me() == ME
    assert client._token == "token-2"
    assert len(calls) == 2
    signins = [req for req in aioclient_mock.mock_calls if str(req[1]).endswith("/sign-in")]
    assert len(signins) == 2


async def test_signin_action_rediscovered(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """When the known action ID stops working, it is read again from the web app."""
    new_id = "ab" * 21
    attempts: list[int] = []

    async def signin(method, url, data):
        attempts.append(1)
        if len(attempts) == 1:
            return AiohttpClientMockResponse(
                method, url, status=500, text='0:{"a":"$@1"}\n1:E{"digest":"123"}\n'
            )
        return AiohttpClientMockResponse(
            method,
            url,
            text=SIGNIN_OK_BODY,
            headers={"x-action-redirect": "/stations;push"},
            cookies={SESSION_COOKIE: "token-new"},
        )

    aioclient_mock.post(f"{BASE_URL}/sign-in", side_effect=signin)
    aioclient_mock.get(
        f"{BASE_URL}/sign-in",
        text='<script src="/_next/static/chunks/aaa.js"></script>'
        '<script src="/_next/static/chunks/bbb.js"></script>',
    )
    aioclient_mock.get(f"{BASE_URL}/_next/static/chunks/aaa.js", text="nothing here")
    aioclient_mock.get(
        f"{BASE_URL}/_next/static/chunks/bbb.js",
        text=f'let i=(0,a.createServerReference)("{new_id}",a.callServer,void 0,'
        'a.findSourceMapURL,"signIn");',
    )

    client = TohmoClient(async_get_clientsession(hass), "me@example.com", "secret")
    assert client._action_id == DEFAULT_SIGNIN_ACTION_ID
    await client.login()
    assert client._action_id == new_id
    assert client._token == "token-new"
