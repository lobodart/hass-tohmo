"""Fixtures for Tohmo tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tohmo.api import SESSION_COOKIE
from custom_components.tohmo.const import BASE_URL, DOMAIN

USER_ID = "user-1"
STATION_ID = "station-1"

ME = {"id": USER_ID, "email": "me@example.com", "tenant": "tohmo"}
STATIONS = {
    "data": [
        {
            "id": STATION_ID,
            "cpoStationId": "FRXXXP0001",
            "label": "Borne garage",
            "status": "active",
            "type": "direct",
            "power": "7.4",
        }
    ],
    "page": 1,
    "nextPage": None,
}
STATE = {
    "stationId": STATION_ID,
    "type": "direct",
    "status": None,
    "chargingMode": "eco",
    "sessionStatus": None,
    "lastHeartbeatAt": None,
    "ecoSchedules": [{"from": "01:26", "to": "07:56"}, {"from": "12:26", "to": "13:56"}],
    "currentSession": None,
    "availableActions": ["toggle-continuous", "toggle-eco"],
}
SESSIONS = {
    "data": [
        {
            "id": "s2",
            "stationId": STATION_ID,
            "startedAt": "2026-11-17T01:26:01.000Z",
            "endedAt": "2026-11-17T07:55:00.000Z",
            "kwhDelivered": 24.32,
            "cost": 4.13,
            "currency": "EUR",
            "tariff": {"period": "off_peak", "pricePerKwh": 0.17},
        },
        {
            "id": "s1",
            "stationId": STATION_ID,
            "startedAt": "2022-11-22T19:45:00.000Z",
            "endedAt": "2022-11-22T22:00:00.000Z",
            "kwhDelivered": 10.5,
            "cost": None,
            "currency": "EUR",
            "tariff": {"period": "peak", "pricePerKwh": 0.2},
        },
    ],
    "page": 1,
    "pageSize": 200,
    "nextPage": None,
}
BILLING = {
    "billingPeriod": {"from": "2026-11-04", "to": "2026-12-03"},
    "consumption": {"kwh": 54.72, "deltaPct": -33},
    "cost": {"amount": 9.29, "currency": "EUR", "deltaPct": -34},
    "sessions": {"count": 3, "deltaPct": -50},
}

PREFERENCES = {"stationSettings": {STATION_ID: {"label": "Domicile", "color": "accent"}}}
CONTRACTS = {
    "data": [
        {
            "id": "c1",
            "kind": "subscription",
            "status": "active",
            "startDate": "2022-11-21T00:00:00.000Z",
            "relatedResource": {"kind": "station", "id": STATION_ID},
            "item": {
                "planCode": "SKU-1",
                "offer": "pay_as_you_go",
                "billingCycle": "monthly",
                "monthlyFee": 12.9,
                "pricePerKwhPeak": 0.28,
                "pricePerKwhOffPeak": 0.17,
                "currency": "EUR",
            },
        }
    ],
    "nextPage": None,
}
INVOICES = {
    "data": [
        {"id": "i3", "type": "subscription", "status": "pending", "amount": 35.62,
         "currency": "EUR", "issuedAt": "2026-11-04T00:00:00.000Z",
         "dueAt": "2026-11-04T00:00:00.000Z", "paidAt": None},
        {"id": "i2", "type": "subscription", "status": "pending", "amount": 33.01,
         "currency": "EUR", "issuedAt": "2026-10-04T00:00:00.000Z",
         "dueAt": "2026-10-04T00:00:00.000Z", "paidAt": None},
        {"id": "i1", "type": "subscription", "status": "paid", "amount": 20.0,
         "currency": "EUR", "issuedAt": "2026-09-04T00:00:00.000Z",
         "dueAt": "2026-09-04T00:00:00.000Z", "paidAt": "2026-09-10T00:00:00.000Z"},
    ],
    "nextPage": None,
}

SIGNIN_OK_BODY = '0:{"a":"$@1"}\n'
SIGNIN_BAD_CREDENTIALS_BODY = '0:{"a":"$@1"}\n1:{"error":"INVALID_CREDENTIALS"}\n'


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Any, enable_custom_integrations: None
) -> None:
    """Enable custom integrations, with a recorder for the statistics import."""


@pytest.fixture(autouse=True)
def mock_validation_session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> Generator[None]:
    """Route the config flow's own HTTP session through the mocker."""
    with patch(
        "custom_components.tohmo.config_flow.create_validation_session",
        side_effect=lambda: aioclient_mock.create_session(hass.loop),
    ):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="me@example.com",
        unique_id=USER_ID,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"},
    )


def mock_signin(
    aioclient_mock: AiohttpClientMocker,
    *,
    ok: bool = True,
    token: str = "token-1",
) -> None:
    """Mock the signIn server action."""
    if ok:
        aioclient_mock.post(
            f"{BASE_URL}/sign-in",
            text=SIGNIN_OK_BODY,
            headers={"x-action-redirect": "/stations;push"},
            cookies={SESSION_COOKIE: token},
        )
    else:
        aioclient_mock.post(f"{BASE_URL}/sign-in", text=SIGNIN_BAD_CREDENTIALS_BODY)


def mock_api(
    aioclient_mock: AiohttpClientMocker, overrides: dict[str, Any] | None = None
) -> None:
    """Mock all data endpoints."""
    responses = {
        "/api/me": ME,
        "/api/stations": STATIONS,
        f"/api/stations/{STATION_ID}/state": STATE,
        "/api/sessions": SESSIONS,
        "/api/analytics/billing-summary": BILLING,
        "/api/me/preferences": PREFERENCES,
        "/api/contracts": CONTRACTS,
        "/api/invoices": INVOICES,
        **(overrides or {}),
    }
    for path, body in responses.items():
        if isinstance(body, int):  # An HTTP error status.
            aioclient_mock.get(f"{BASE_URL}{path}", status=body)
        else:
            aioclient_mock.get(f"{BASE_URL}{path}", json=body)


@pytest.fixture
def mock_tohmo(aioclient_mock: AiohttpClientMocker) -> Generator[AiohttpClientMocker]:
    """Mock a working Tohmo account."""
    mock_signin(aioclient_mock)
    mock_api(aioclient_mock)
    yield aioclient_mock


