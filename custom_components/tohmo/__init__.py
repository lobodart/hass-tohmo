"""The Tohmo integration."""

from __future__ import annotations

import aiohttp

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import TohmoClient
from .coordinator import TohmoConfigEntry, TohmoCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SELECT, Platform.SENSOR]


def create_client(hass: HomeAssistant, email: str, password: str) -> TohmoClient:
    """Create a client with its own cookie-less HTTP session."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    return TohmoClient(session, email, password)


async def async_setup_entry(hass: HomeAssistant, entry: TohmoConfigEntry) -> bool:
    """Set up Tohmo from a config entry."""
    client = create_client(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = TohmoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TohmoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
