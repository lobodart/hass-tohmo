"""Base entities for Tohmo."""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import StationData, TohmoCoordinator
from .eco import EcoStatus, eco_status


class TohmoEntity(CoordinatorEntity[TohmoCoordinator]):
    """Base entity."""

    _attr_has_entity_name = True


class TohmoStationEntity(TohmoEntity):
    """Entity attached to a charging station."""

    def __init__(self, coordinator: TohmoCoordinator, station_id: str, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.station_id = station_id
        self._attr_unique_id = f"{station_id}_{key}"
        station = self.station_data.station
        power = station.get("power")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station_id)},
            name=self.station_data.name or station.get("label") or "Tohmo station",
            manufacturer="Tohmo",
            model=f"Charging station {power} kW" if power else "Charging station",
            serial_number=station.get("cpoStationId"),
        )

    @property
    def station_data(self) -> StationData:
        """Return the data of this station."""
        return self.coordinator.data.stations[self.station_id]

    @property
    def available(self) -> bool:
        """Return if the station is still part of the account."""
        return super().available and self.station_id in self.coordinator.data.stations


class TohmoEcoEntity(TohmoStationEntity):
    """Station entity derived from the eco schedule.

    Its state depends on the clock, so besides coordinator updates it is
    refreshed at the exact start and end of each eco window.
    """

    _unsub_timer: CALLBACK_TYPE | None = None

    @property
    def eco_status(self) -> EcoStatus:
        """Return the eco status now."""
        data = self.station_data
        return eco_status((data.state or {}).get("ecoSchedules"), dt_util.now(), data.tz)

    async def async_added_to_hass(self) -> None:
        """Schedule the first refresh."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_timer)
        self._schedule_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Reschedule when the schedule may have changed."""
        self._schedule_refresh()
        super()._handle_coordinator_update()

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _schedule_refresh(self) -> None:
        self._cancel_timer()
        if self.station_id not in self.coordinator.data.stations:
            return
        if (when := self.eco_status.next_change) is not None:
            self._unsub_timer = async_track_point_in_time(self.hass, self._handle_timer, when)

    @callback
    def _handle_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._schedule_refresh()
        self.async_write_ha_state()


class TohmoAccountEntity(TohmoEntity):
    """Entity attached to the Tohmo account."""

    def __init__(self, coordinator: TohmoCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        user_id = coordinator.config_entry.unique_id
        self._attr_unique_id = f"{user_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, user_id)},
            translation_key="account",
            manufacturer="Tohmo",
            model="Account",
        )
