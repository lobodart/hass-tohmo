"""Charging mode selector for Tohmo stations."""

from __future__ import annotations

import asyncio
import time

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CHARGING_MODES, TohmoError
from .const import DOMAIN, LOGGER
from .coordinator import TohmoConfigEntry, TohmoCoordinator
from .entity import TohmoStationEntity

PARALLEL_UPDATES = 1

# Action names listed in `availableActions` for each mode.
MODE_ACTIONS = {"eco": "toggle-eco", "continuous": "toggle-continuous"}
# How long to wait for the station to confirm a change, and how often to check.
CONFIRM_TIMEOUT = 60
POLL_INTERVAL = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TohmoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the charging mode selectors."""
    coordinator = entry.runtime_data
    async_add_entities(
        TohmoChargingModeSelect(coordinator, station_id)
        for station_id, data in coordinator.data.stations.items()
        if data.state is not None
    )


class TohmoChargingModeSelect(TohmoStationEntity, SelectEntity):
    """Switch a station between eco (off-peak only) and continuous charging."""

    _attr_translation_key = "charging_mode"
    _attr_options = list(CHARGING_MODES)

    def __init__(self, coordinator: TohmoCoordinator, station_id: str) -> None:
        """Initialize the selector."""
        super().__init__(coordinator, station_id, "charging_mode")

    @property
    def _state(self) -> dict:
        return self.station_data.state or {}

    @property
    def current_option(self) -> str | None:
        """Return the current charging mode."""
        mode = self._state.get("chargingMode")
        return mode if mode in CHARGING_MODES else None

    @property
    def available(self) -> bool:
        """Return if Tohmo currently lets this station change mode."""
        actions = self._state.get("availableActions") or []
        return super().available and any(a in actions for a in MODE_ACTIONS.values())

    async def async_select_option(self, option: str) -> None:
        """Ask the station to change mode and wait for its confirmation."""
        if option == self.current_option:
            return
        if MODE_ACTIONS[option] not in (self._state.get("availableActions") or []):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mode_not_available",
                translation_placeholders={"mode": option},
            )

        client = self.coordinator.client
        # The web app tells its own request apart from older ones this way.
        previous = (self._state.get("lastAction") or {}).get("requestedAt")
        try:
            status = (await client.set_charging_mode(self.station_id, option)).get("status")
            deadline = time.monotonic() + CONFIRM_TIMEOUT
            while status not in ("completed", "failed") and time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                state = await client.get_station_state(self.station_id)
                last = state.get("lastAction") or {}
                if last.get("action") == option and last.get("requestedAt") != previous:
                    status = last.get("status")
        except TohmoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mode_change_error",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()

        if status == "failed":
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mode_change_failed",
                translation_placeholders={"mode": option},
            )
        if status != "completed":
            # Not an error: the station may still apply it, the next refresh
            # will show the actual mode.
            LOGGER.warning(
                "Station %s did not confirm the switch to %s within %s s",
                self.station_id,
                option,
                CONFIRM_TIMEOUT,
            )
