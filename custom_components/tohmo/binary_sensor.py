"""Binary sensors for Tohmo."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TohmoConfigEntry, TohmoCoordinator
from .entity import TohmoEcoEntity, TohmoStationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TohmoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Tohmo binary sensors."""
    coordinator = entry.runtime_data
    stations = [
        station_id
        for station_id, data in coordinator.data.stations.items()
        if data.state is not None
    ]
    entities: list[BinarySensorEntity] = [
        TohmoChargingSensor(coordinator, station_id) for station_id in stations
    ]
    entities.extend(TohmoOffPeakSensor(coordinator, station_id) for station_id in stations)
    async_add_entities(entities)


class TohmoOffPeakSensor(TohmoEcoEntity, BinarySensorEntity):
    """Whether an eco window (off-peak hours) is in progress."""

    _attr_translation_key = "off_peak"

    def __init__(self, coordinator: TohmoCoordinator, station_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, station_id, "off_peak")

    @property
    def is_on(self) -> bool:
        """Return true during an eco window."""
        return self.eco_status.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the configured windows."""
        schedules = (self.station_data.state or {}).get("ecoSchedules") or []
        return {"windows": [f"{slot.get('from')}-{slot.get('to')}" for slot in schedules]}


class TohmoChargingSensor(TohmoStationEntity, BinarySensorEntity):
    """Whether a charging session is in progress on the station."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_translation_key = "charging"

    def __init__(self, coordinator: TohmoCoordinator, station_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, station_id, "charging")

    @property
    def is_on(self) -> bool:
        """Return true if a session is in progress."""
        return bool((self.station_data.state or {}).get("currentSession"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw live state fields, until their values are documented."""
        state = self.station_data.state or {}
        return {
            "station_status": state.get("status"),
            "session_status": state.get("sessionStatus"),
            "current_session": state.get("currentSession"),
        }
