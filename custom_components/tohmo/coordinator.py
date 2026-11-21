"""Data update coordinator for Tohmo."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TohmoAuthError, TohmoClient, TohmoError
from .const import DOMAIN, LOGGER, TOHMO_TIME_ZONE, UPDATE_INTERVAL
from .statistics import async_import_station_statistics
from .util import parse_session_time

type TohmoConfigEntry = ConfigEntry[TohmoCoordinator]


@dataclass
class StationData:
    """Aggregated data for one charging station."""

    station: dict[str, Any]
    state: dict[str, Any] | None
    total_kwh: float
    total_cost: float
    currency: str | None
    session_count: int
    last_session: dict[str, Any] | None
    last_started_at: datetime | None
    last_ended_at: datetime | None
    tz: tzinfo
    # Name given to the station in the app (e.g. "Home"), if any.
    name: str | None = None
    # Active subscription of the station (prices, monthly fee), if known.
    contract: dict[str, Any] | None = None


@dataclass
class TohmoData:
    """Data returned by the coordinator."""

    stations: dict[str, StationData] = field(default_factory=dict)
    billing_summary: dict[str, Any] | None = None
    # None when invoices could not be fetched.
    invoices: list[dict[str, Any]] | None = None


def _active_contract(
    contracts: list[dict[str, Any]], station_id: str
) -> dict[str, Any] | None:
    """Return the active subscription of a station."""
    for contract in contracts:
        resource = contract.get("relatedResource") or {}
        if resource.get("id") == station_id and contract.get("status") == "active":
            return contract
    return None


class TohmoCoordinator(DataUpdateCoordinator[TohmoData]):
    """Fetch stations, live state and charging sessions."""

    config_entry: TohmoConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: TohmoConfigEntry, client: TohmoClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.user: dict[str, Any] = {}
        # Every session seen so far, by ID. Filled completely on the first
        # refresh, then kept up to date from the most recent page.
        self._sessions: dict[str, dict[str, Any]] = {}
        self._history_loaded = False
        self._statistics_signatures: dict[str, int] = {}
        self.tz: tzinfo = dt_util.UTC

    async def _async_setup(self) -> None:
        """Load the time zone of Tohmo schedules and session times."""
        self.tz = await dt_util.async_get_time_zone(TOHMO_TIME_ZONE) or dt_util.UTC

    async def _async_update_data(self) -> TohmoData:
        try:
            if not self.user:
                self.user = await self.client.get_me()
            stations = await self.client.get_stations()
            await self._async_update_sessions()
            states = {
                station["id"]: await self._async_get_state(station["id"])
                for station in stations
            }
            billing_summary = await self.client.get_billing_summary()
            preferences = await self._optional(self.client.get_preferences)
            contracts = await self._optional(self.client.get_contracts)
            invoices = await self._optional(self.client.get_invoices)
        except TohmoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TohmoError as err:
            raise UpdateFailed(str(err)) from err

        settings = (preferences or {}).get("stationSettings") or {}
        data = TohmoData(billing_summary=billing_summary, invoices=invoices)
        for station in stations:
            station_id = station["id"]
            aggregated = self._aggregate(station, states[station_id])
            aggregated.name = (settings.get(station_id) or {}).get("label") or None
            aggregated.contract = _active_contract(contracts or [], station_id)
            data.stations[station_id] = aggregated
            self._async_update_statistics(aggregated)
        return data

    def _async_update_statistics(self, data: StationData) -> None:
        """Re-import hourly statistics when finished sessions changed."""
        station_id = data.station["id"]
        sessions = [
            s
            for s in self._sessions.values()
            if s.get("stationId") == station_id and s.get("endedAt")
        ]
        signature = hash(
            tuple(
                sorted(
                    (s["id"], s.get("kwhDelivered"), s.get("cost"), s.get("endedAt"))
                    for s in sessions
                )
            )
        )
        if self._statistics_signatures.get(station_id) == signature:
            return
        self._statistics_signatures[station_id] = signature
        async_import_station_statistics(
            self.hass,
            data.station,
            data.name or data.station.get("label") or station_id,
            sessions,
            (data.contract or {}).get("item", {}).get("currency") or data.currency or "EUR",
            self.tz,
        )

    async def _optional[T](self, fetch: Callable[[], Awaitable[T]]) -> T | None:
        """Fetch secondary data; its failure must not make every entity unavailable."""
        try:
            return await fetch()
        except TohmoAuthError:
            raise
        except TohmoError as err:
            LOGGER.debug("Could not fetch %s: %s", fetch.__name__, err)
            return None

    async def _async_get_state(self, station_id: str) -> dict[str, Any] | None:
        """Return the live state, or None if this station does not expose it."""
        try:
            return await self.client.get_station_state(station_id)
        except TohmoAuthError:
            raise
        except TohmoError as err:
            LOGGER.debug("No live state for station %s: %s", station_id, err)
            return None

    async def _async_update_sessions(self) -> None:
        """Fetch new and updated sessions.

        Pages are sorted by start date, most recent first. After the first
        full load, stop at the first page that only contains known sessions.
        """
        page: int | None = 1
        while page:
            data = await self.client.get_sessions_page(page)
            sessions = data.get("data") or []
            known = all(session["id"] in self._sessions for session in sessions)
            for session in sessions:
                self._sessions[session["id"]] = session
            if known and self._history_loaded:
                return
            page = data.get("nextPage")
        self._history_loaded = True

    def _aggregate(
        self, station: dict[str, Any], state: dict[str, Any] | None
    ) -> StationData:
        sessions = [
            s for s in self._sessions.values() if s.get("stationId") == station["id"]
        ]
        last = max(sessions, key=lambda s: s.get("startedAt") or "", default=None)
        currencies = {s["currency"] for s in sessions if s.get("currency")}
        return StationData(
            station=station,
            state=state,
            total_kwh=round(sum(s.get("kwhDelivered") or 0 for s in sessions), 3),
            total_cost=round(sum(s.get("cost") or 0 for s in sessions), 2),
            currency=currencies.pop() if len(currencies) == 1 else None,
            session_count=len(sessions),
            last_session=last,
            last_started_at=parse_session_time((last or {}).get("startedAt"), self.tz),
            last_ended_at=parse_session_time((last or {}).get("endedAt"), self.tz),
            tz=self.tz,
        )
