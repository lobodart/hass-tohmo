"""Client for the (undocumented) Tohmo web API used by app.go-tohmo.com.

Tohmo has no public API. Authentication goes through the `signIn` Next.js
server action of the web app, which sets a Neon Auth session cookie valid for
7 days. Data comes from the JSON endpoints under /api, authenticated with that
cookie and the X-Tenant-Slug header.

When the session expires (HTTP 401), the client signs in again with the stored
credentials. The server action ID is tied to a web app build: when it stops
working, the client re-discovers it from the sign-in page JavaScript.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp

from .const import BASE_URL, LOGGER, TENANT_SLUG

# Server action ID of `signIn` observed on 2026-11-20. Used as a first guess
# to avoid scraping the JavaScript bundles on every login.
DEFAULT_SIGNIN_ACTION_ID = "40bcb48014670c3d4406a316c6f9d39afaafcff26c"

SESSION_COOKIE = "__Secure-neon-auth.session_token"
USER_AGENT = "Mozilla/5.0 (compatible; HomeAssistant-Tohmo)"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
PAGE_SIZE = 200  # Server-side maximum.

_CHUNK_RE = re.compile(r"/_next/static/chunks/[A-Za-z0-9_.~-]+\.js")
_SIGNIN_ACTION_RE = re.compile(
    r'createServerReference\)\("([0-9a-f]{20,})"[^)]{0,200}?"signIn"\)'
)


class TohmoError(Exception):
    """Base error for the Tohmo client."""


class TohmoConnectionError(TohmoError):
    """Network error or unexpected HTTP response."""


class TohmoAuthError(TohmoError):
    """The credentials were rejected."""


class TohmoProtocolError(TohmoError):
    """The web app no longer behaves as this client expects."""


class TohmoClient:
    """Minimal async client for the Tohmo web API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize the client.

        The aiohttp session should not share its cookie jar with other
        integrations: the session cookie is handled explicitly.
        """
        self._session = session
        self._email = email
        self._password = password
        self._base_url = base_url
        self._token: str | None = None
        self._action_id = DEFAULT_SIGNIN_ACTION_ID
        self._login_lock = asyncio.Lock()

    # ------------------------------------------------------------------ auth

    async def login(self) -> None:
        """Sign in and store the session token.

        Raises TohmoAuthError if the credentials are rejected.
        """
        async with self._login_lock:
            await self._login()

    async def _login(self) -> None:
        try:
            self._token = await self._signin(self._action_id)
            return
        except TohmoProtocolError as err:
            LOGGER.debug("Sign-in with action %s failed (%s), rediscovering", self._action_id, err)

        action_id = await self._discover_signin_action_id()
        if action_id == self._action_id:
            raise TohmoProtocolError("Sign-in failed and no newer sign-in action was found")
        self._action_id = action_id
        self._token = await self._signin(action_id)

    async def _signin(self, action_id: str) -> str:
        """Call the signIn server action and return the session token."""
        # React encodes a FormData argument as `_<ref>_<field>` parts plus a
        # root `0` part referencing it.
        writer = aiohttp.MultipartWriter("form-data")
        for name, value in (
            ("_1_email", self._email),
            ("_1_password", self._password),
            ("_1_rememberMe", "true"),
            ("0", '["$K1"]'),
        ):
            part = writer.append(value)
            part.set_content_disposition("form-data", name=name)

        headers = {
            "Next-Action": action_id,
            "Accept": "text/x-component",
            "Origin": self._base_url,
            "Referer": f"{self._base_url}/sign-in",
            "User-Agent": USER_AGENT,
        }
        try:
            async with self._session.post(
                f"{self._base_url}/sign-in",
                data=writer,
                headers=headers,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.text()
                redirect = resp.headers.get("x-action-redirect")
                cookie = resp.cookies.get(SESSION_COOKIE)
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TohmoConnectionError(f"Sign-in request failed: {err}") from err

        if "INVALID_CREDENTIALS" in body:
            raise TohmoAuthError("Invalid email or password")
        if status == 200 and redirect and cookie is not None and cookie.value:
            return cookie.value
        if status >= 500 and "digest" not in body:
            raise TohmoConnectionError(f"Sign-in returned HTTP {status}")
        raise TohmoProtocolError(f"Unexpected sign-in response (HTTP {status}): {body[-200:]!r}")

    async def _discover_signin_action_id(self) -> str:
        """Find the current signIn server action ID in the sign-in page bundles."""
        page = await self._get_text(f"{self._base_url}/sign-in")
        for chunk in dict.fromkeys(_CHUNK_RE.findall(page)):
            script = await self._get_text(f"{self._base_url}{chunk}")
            if match := _SIGNIN_ACTION_RE.search(script):
                LOGGER.debug("Found signIn action %s in %s", match.group(1), chunk)
                return match.group(1)
        raise TohmoProtocolError("signIn server action not found in the web app")

    async def _get_text(self, url: str) -> str:
        try:
            async with self._session.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TohmoConnectionError(f"GET {url} failed: {err}") from err

    # ------------------------------------------------------------------- api

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an API path, signing in again once if the session expired."""
        if self._token is None:
            async with self._login_lock:
                if self._token is None:
                    await self._login()

        for attempt in (1, 2):
            token = self._token
            status, data = await self._get_json(path, params, token)
            if status != 401:
                break
            if attempt == 2:
                raise TohmoProtocolError(f"{path} still unauthorized after signing in again")
            LOGGER.debug("Session expired, signing in again")
            async with self._login_lock:
                # Another call may already have refreshed the token.
                if self._token == token:
                    await self._login()

        if status >= 400:
            raise TohmoConnectionError(f"{path} returned HTTP {status}")
        return data

    async def _get_json(
        self, path: str, params: dict[str, Any] | None, token: str | None
    ) -> tuple[int, Any]:
        headers = {
            "X-Tenant-Slug": TENANT_SLUG,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Cookie": f"{SESSION_COOKIE}={token}",
        }
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                params=params,
                headers=headers,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    return resp.status, None
                return resp.status, await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TohmoConnectionError(f"GET {path} failed: {err}") from err

    async def get_me(self) -> dict[str, Any]:
        """Return the current user."""
        return await self._request("/api/me")

    async def get_stations(self) -> list[dict[str, Any]]:
        """Return all charging stations of the account."""
        return await self._get_all("/api/stations")

    async def get_station_state(self, station_id: str) -> dict[str, Any]:
        """Return the live state of a station."""
        return await self._request(f"/api/stations/{station_id}/state")

    async def get_sessions_page(self, page: int = 1) -> dict[str, Any]:
        """Return one page of charging sessions, most recent first."""
        return await self._request(
            "/api/sessions", {"page": page, "pageSize": PAGE_SIZE}
        )

    async def get_billing_summary(self) -> dict[str, Any]:
        """Return consumption and cost for the current billing period."""
        return await self._request("/api/analytics/billing-summary")

    async def get_preferences(self) -> dict[str, Any]:
        """Return user preferences, including the station names shown in the app."""
        return await self._request("/api/me/preferences")

    async def get_contracts(self) -> list[dict[str, Any]]:
        """Return the subscriptions (price per kWh, monthly fee) of the account."""
        return await self._get_all("/api/contracts")

    async def get_invoices(self) -> list[dict[str, Any]]:
        """Return all invoices."""
        return await self._get_all("/api/invoices")

    async def _get_all(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page: int | None = 1
        while page:
            data = await self._request(path, {"page": page, "pageSize": PAGE_SIZE})
            items.extend(data.get("data") or [])
            page = data.get("nextPage")
        return items
