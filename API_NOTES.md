# Tohmo internal API (reverse-engineered from app.go-tohmo.com)

Observed on 2026-11-20 (Next.js build `TlK3-LKUTX8-Sy52upCpd`). Undocumented API: may break with any deployment.

## Authentication
- Sign-in is the Next.js server action `signIn`: `POST https://app.go-tohmo.com/sign-in`
  - Headers: `Next-Action: 40bcb48014670c3d4406a316c6f9d39afaafcff26c`, `Accept: text/x-component`
  - Multipart body: `_1_email`, `_1_password`, `_1_rememberMe=true`, `0=["$K1"]`
  - Success: header `x-action-redirect: /stations;push` and cookies
    `__Secure-neon-auth.session_token` (~7 days, not sliding) and `__Secure-neon-auth.local.session_data` (~10 min)
  - Failure: no redirect, RSC payload containing `{"error":"INVALID_CREDENTIALS"}` (or `INVALID_INPUT` if the body is badly encoded)
  - Unknown action ID: HTTP 404 "Server action not found".
  - ⚠️ The action ID may change with each build: find it dynamically
    (JS chunk of /sign-in: `createServerReference)("<id>", ..., "signIn")`).
- API calls: header `X-Tenant-Slug: tohmo` and cookie `__Secure-neon-auth.session_token` are enough.
  Send a User-Agent (the default Python one gets a Cloudflare 403).
- Unauthenticated: `401 {"success":false,"error":{"code":"UNAUTHORIZED"}}`.

## Endpoints (GET unless stated otherwise)
| Endpoint | Content |
|---|---|
| `/api/me` | profile |
| `/api/stations` | paginated list (`data`, `page`, `pageSize`, `totalCount`, `nextPage`); station: `id`, `cpoStationId`, `label`, `power` ("3.7"), `type` ("direct"), `status` |
| `/api/stations/{id}/state` | `chargingMode` (eco/continuous), `sessionStatus`, `status`, `currentSession`, `ecoSchedules` [{from,to}], `availableActions`, `lastAction`, `lastHeartbeatAt`. Headers `etag`, `x-poll-interval` |
| `/api/sessions` | paginated (50 per page by default, `pageSize` up to 200), most recent first; session: `startedAt`, `endedAt`, `kwhDelivered`, `cost`, `currency`, `billingMode`, `tariff{period,pricePerKwh}`, `stationId`, `scope` |
| `/api/analytics/consumption-breakdown?groupBy=day|month&from=YYYY-MM-DD&to=YYYY-MM-DD` | buckets `peakKwh`/`offPeakKwh`/`totalKwh`, `offPeakWindow` |
| `/api/analytics/billing-summary` | current billing period: kWh, cost, session count, deltas |
| `/api/analytics/environment-impact` | CO2, fuel saved |
| `/api/contracts` | contract per station (`relatedResource.id`): `status`, `startDate`, `item{offer, monthlyFee, pricePerKwhPeak, pricePerKwhOffPeak, currency}` |
| `/api/invoices` | invoices: `amount`, `currency`, `status` (paid/pending), `type` (subscription/installation), `issuedAt`, `dueAt`, `paidAt`; PDF: `/api/invoices/{id}/pdf` |
| `/api/me/preferences` | `stationSettings[stationId].label` = station name shown in the app |
| `POST /api/stations/{id}/actions/{action}` | actions: `eco`, `continuous`, `launch-session`, `stop-session`, `unlock-cable`, `reboot`, `toggle-auth`, `enforce-power`; only those listed in `availableActions` are allowed (e.g. toggle-eco/toggle-continuous). **Never tested.** |

## Time zones (checked on more than 1,000 sessions)
- `ecoSchedules`: French local times ("01:26"), as shown in the app.
- Session `startedAt` / `endedAt`: local time **wrongly tagged `Z`**; eco sessions start at `01:26:01Z` in summer and winter alike.
  Interpret them in Europe/Paris.

## Session tariff
`tariff.period` (peak/off_peak) is set per session and does not follow `ecoSchedules` (sessions at 18:15 marked off_peak, at 01:15 marked peak): no "current price" can be derived from it.

## Pricing history (invoices)
- Flat-rate offers (older, until around late 2024): fixed monthly amount, no cost per session (`cost` null). The `tariff` of those sessions is retroactive, not real.
- Subscription + per-kWh offers (since around late 2024): session `cost` = amount billed, including VAT.
- Invoices use a rolling period (a session may land on the next invoice); the session list in the PDF is authoritative.
