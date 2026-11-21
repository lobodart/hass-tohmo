"""Sensors for Tohmo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    StationData,
    TohmoConfigEntry,
    TohmoCoordinator,
    TohmoData,
)
from .util import parse_datetime
from .eco import parse_time
from .entity import TohmoAccountEntity, TohmoEcoEntity, TohmoStationEntity

PARALLEL_UPDATES = 0

type StateType = str | int | float | datetime | None


def _last(key: str) -> Callable[[StationData], Any]:
    return lambda data: (data.last_session or {}).get(key)


def _last_duration(data: StationData) -> float | None:
    if data.last_started_at is None or data.last_ended_at is None:
        return None
    return round((data.last_ended_at - data.last_started_at).total_seconds() / 60)


def _last_tariff(data: StationData) -> dict[str, Any]:
    tariff = (data.last_session or {}).get("tariff") or {}
    return {
        "tariff_period": tariff.get("period"),
        "price_per_kwh": tariff.get("pricePerKwh"),
    }


def _state(key: str) -> Callable[[StationData], Any]:
    return lambda data: (data.state or {}).get(key)


@dataclass(frozen=True, kw_only=True)
class TohmoStationSensorDescription(SensorEntityDescription):
    """Describes a station sensor."""

    value_fn: Callable[[StationData], StateType]
    attrs_fn: Callable[[StationData], dict[str, Any]] | None = None
    monetary: bool = False
    # Unit computed from the data (e.g. the contract currency per kWh).
    unit_fn: Callable[[StationData], str] | None = None
    exists_fn: Callable[[StationData], bool] = lambda _: True


def _contract_item(key: str) -> Callable[[StationData], Any]:
    return lambda data: ((data.contract or {}).get("item") or {}).get(key)


def _contract_currency(data: StationData) -> str:
    return _contract_item("currency")(data) or data.currency or "EUR"


def _contract_attrs(data: StationData) -> dict[str, Any]:
    contract = data.contract or {}
    item = contract.get("item") or {}
    return {
        "offer": item.get("offer"),
        "plan_code": item.get("planCode"),
        "billing_cycle": item.get("billingCycle"),
        "start_date": (contract.get("startDate") or "")[:10] or None,
    }


STATION_SENSORS: tuple[TohmoStationSensorDescription, ...] = (
    TohmoStationSensorDescription(
        key="energy_total",
        translation_key="energy_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda data: data.total_kwh,
        attrs_fn=lambda data: {"session_count": data.session_count},
    ),
    TohmoStationSensorDescription(
        key="cost_total",
        translation_key="cost_total",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        monetary=True,
        value_fn=lambda data: data.total_cost,
    ),
    TohmoStationSensorDescription(
        key="last_session_energy",
        translation_key="last_session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_last("kwhDelivered"),
        attrs_fn=_last_tariff,
    ),
    TohmoStationSensorDescription(
        key="last_session_cost",
        translation_key="last_session_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        monetary=True,
        value_fn=_last("cost"),
        attrs_fn=_last_tariff,
    ),
    TohmoStationSensorDescription(
        key="last_session_start",
        translation_key="last_session_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.last_started_at,
    ),
    TohmoStationSensorDescription(
        key="last_session_end",
        translation_key="last_session_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.last_ended_at,
    ),
    TohmoStationSensorDescription(
        key="last_session_duration",
        translation_key="last_session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_last_duration,
    ),
    TohmoStationSensorDescription(
        key="charging_mode",
        translation_key="charging_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["eco", "continuous"],
        value_fn=_state("chargingMode"),
        attrs_fn=lambda data: {
            "eco_schedules": [
                f"{slot.get('from')}-{slot.get('to')}"
                for slot in (data.state or {}).get("ecoSchedules") or []
            ]
        },
        exists_fn=lambda data: data.state is not None,
    ),
    TohmoStationSensorDescription(
        key="subscription_fee",
        translation_key="subscription_fee",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        unit_fn=_contract_currency,
        value_fn=_contract_item("monthlyFee"),
        attrs_fn=_contract_attrs,
        exists_fn=lambda data: data.contract is not None,
    ),
    TohmoStationSensorDescription(
        key="price_peak",
        translation_key="price_peak",
        suggested_display_precision=2,
        unit_fn=lambda data: f"{_contract_currency(data)}/{UnitOfEnergy.KILO_WATT_HOUR}",
        value_fn=_contract_item("pricePerKwhPeak"),
        exists_fn=lambda data: data.contract is not None,
    ),
    TohmoStationSensorDescription(
        key="price_off_peak",
        translation_key="price_off_peak",
        suggested_display_precision=2,
        unit_fn=lambda data: f"{_contract_currency(data)}/{UnitOfEnergy.KILO_WATT_HOUR}",
        value_fn=_contract_item("pricePerKwhOffPeak"),
        exists_fn=lambda data: data.contract is not None,
    ),
    TohmoStationSensorDescription(
        key="last_heartbeat",
        translation_key="last_heartbeat",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: parse_datetime(_state("lastHeartbeatAt")(data)),
        exists_fn=lambda data: data.state is not None,
    ),
)


def _billing(*path: str) -> Callable[[TohmoData], Any]:
    def get(data: TohmoData) -> Any:
        value: Any = data.billing_summary
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        return value

    return get


def _period_attrs(data: TohmoData) -> dict[str, Any]:
    period = (data.billing_summary or {}).get("billingPeriod") or {}
    return {"period_start": period.get("from"), "period_end": period.get("to")}


def _last_invoice(data: TohmoData) -> dict[str, Any] | None:
    return max(data.invoices or [], key=lambda i: i.get("issuedAt") or "", default=None)


def _pending_invoices(data: TohmoData) -> list[dict[str, Any]]:
    return [i for i in data.invoices or [] if i.get("status") == "pending"]


def _invoice_currency(data: TohmoData) -> str:
    return (_last_invoice(data) or {}).get("currency") or "EUR"


def _last_invoice_attrs(data: TohmoData) -> dict[str, Any]:
    invoice = _last_invoice(data) or {}
    return {
        "issued_at": (invoice.get("issuedAt") or "")[:10] or None,
        "due_at": (invoice.get("dueAt") or "")[:10] or None,
        "paid_at": (invoice.get("paidAt") or "")[:10] or None,
        "status": invoice.get("status"),
        "type": invoice.get("type"),
    }


def _pending_attrs(data: TohmoData) -> dict[str, Any]:
    pending = _pending_invoices(data)
    return {
        "total_amount": round(sum(i.get("amount") or 0 for i in pending), 2),
        "currency": _invoice_currency(data),
        "invoices": [
            f"{(i.get('issuedAt') or '')[:10]}: {i.get('amount')}" for i in pending
        ],
    }


def _has_billing(data: TohmoData) -> bool:
    return data.billing_summary is not None


def _has_invoices(data: TohmoData) -> bool:
    return data.invoices is not None


@dataclass(frozen=True, kw_only=True)
class TohmoAccountSensorDescription(SensorEntityDescription):
    """Describes an account sensor."""

    value_fn: Callable[[TohmoData], StateType]
    attrs_fn: Callable[[TohmoData], dict[str, Any]] | None = None
    unit_fn: Callable[[TohmoData], str] | None = None
    exists_fn: Callable[[TohmoData], bool] = _has_billing


ACCOUNT_SENSORS: tuple[TohmoAccountSensorDescription, ...] = (
    TohmoAccountSensorDescription(
        key="period_energy",
        translation_key="period_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_billing("consumption", "kwh"),
        attrs_fn=lambda d: {**_period_attrs(d), "change_pct": _billing("consumption", "deltaPct")(d)},
    ),
    TohmoAccountSensorDescription(
        key="period_cost",
        translation_key="period_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=_billing("cost", "amount"),
        unit_fn=lambda d: _billing("cost", "currency")(d) or "EUR",
        attrs_fn=lambda d: {**_period_attrs(d), "change_pct": _billing("cost", "deltaPct")(d)},
    ),
    TohmoAccountSensorDescription(
        key="period_sessions",
        translation_key="period_sessions",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_billing("sessions", "count"),
        attrs_fn=_period_attrs,
    ),
    TohmoAccountSensorDescription(
        key="last_invoice",
        translation_key="last_invoice",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda d: (_last_invoice(d) or {}).get("amount"),
        unit_fn=_invoice_currency,
        attrs_fn=_last_invoice_attrs,
        exists_fn=_has_invoices,
    ),
    TohmoAccountSensorDescription(
        key="pending_invoices",
        translation_key="pending_invoices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_pending_invoices(d)),
        attrs_fn=_pending_attrs,
        exists_fn=_has_invoices,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TohmoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Tohmo sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        TohmoStationSensor(coordinator, station_id, description)
        for station_id, data in coordinator.data.stations.items()
        for description in STATION_SENSORS
        if description.exists_fn(data)
    ]
    entities.extend(
        TohmoNextOffPeakSensor(coordinator, station_id)
        for station_id, data in coordinator.data.stations.items()
        if data.state is not None
    )
    entities.extend(
        TohmoAccountSensor(coordinator, description)
        for description in ACCOUNT_SENSORS
        if description.exists_fn(coordinator.data)
    )
    async_add_entities(entities)

    # One start/end pair per eco window, added as soon as Tohmo returns more.
    known_slots: set[tuple[str, int]] = set()

    @callback
    def add_slot_sensors() -> None:
        new: list[SensorEntity] = []
        for station_id, data in coordinator.data.stations.items():
            for index in range(len(eco_schedules(data))):
                if (station_id, index) in known_slots:
                    continue
                known_slots.add((station_id, index))
                new.extend(
                    TohmoOffPeakSlotSensor(coordinator, station_id, index, bound)
                    for bound in ("start", "end")
                )
        if new:
            async_add_entities(new)

    add_slot_sensors()
    entry.async_on_unload(coordinator.async_add_listener(add_slot_sensors))


def eco_schedules(data: StationData) -> list[dict[str, Any]]:
    """Return the eco windows of a station as listed by Tohmo."""
    return (data.state or {}).get("ecoSchedules") or []


class TohmoNextOffPeakSensor(TohmoEcoEntity, SensorEntity):
    """Start of the next eco window (off-peak hours)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "next_off_peak"

    def __init__(self, coordinator: TohmoCoordinator, station_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, station_id, "next_off_peak")

    @property
    def native_value(self) -> datetime | None:
        """Return when the next eco window starts."""
        return self.eco_status.next_start


class TohmoOffPeakSlotSensor(TohmoStationEntity, SensorEntity):
    """Start or end time ("HH:MM") of one eco window."""

    _attr_icon = "mdi:clock-outline"

    def __init__(
        self, coordinator: TohmoCoordinator, station_id: str, index: int, bound: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, station_id, f"off_peak_{bound}_{index}")
        self._index = index
        self._field = "from" if bound == "start" else "to"
        self._attr_translation_key = f"off_peak_{bound}"
        self._attr_translation_placeholders = {"index": str(index + 1)}

    @property
    def _slot(self) -> dict[str, Any] | None:
        schedules = eco_schedules(self.station_data)
        return schedules[self._index] if self._index < len(schedules) else None

    @property
    def available(self) -> bool:
        """Return False once Tohmo no longer lists this window."""
        return (
            super().available
            and self.station_data.state is not None
            and self._slot is not None
        )

    @property
    def native_value(self) -> str | None:
        """Return the time as "HH:MM"."""
        value = parse_time((self._slot or {}).get(self._field))
        return value.strftime("%H:%M") if value else None


class TohmoStationSensor(TohmoStationEntity, SensorEntity):
    """Sensor of a charging station."""

    entity_description: TohmoStationSensorDescription

    def __init__(
        self,
        coordinator: TohmoCoordinator,
        station_id: str,
        description: TohmoStationSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, station_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the state."""
        return self.entity_description.value_fn(self.station_data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit, the currency for monetary sensors."""
        if self.entity_description.unit_fn is not None:
            return self.entity_description.unit_fn(self.station_data)
        if self.entity_description.monetary:
            return self.station_data.currency or "EUR"
        return super().native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.station_data)


class TohmoAccountSensor(TohmoAccountEntity, SensorEntity):
    """Sensor of the Tohmo account (billing period, invoices)."""

    entity_description: TohmoAccountSensorDescription

    def __init__(
        self, coordinator: TohmoCoordinator, description: TohmoAccountSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit, the currency for monetary sensors."""
        if self.entity_description.unit_fn is not None:
            return self.entity_description.unit_fn(self.coordinator.data)
        return super().native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
