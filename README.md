# Tohmo for Home Assistant

Unofficial integration for [Tohmo](https://go-tohmo.com) (formerly Zeplug) EV charging stations: energy, costs and charger state in Home Assistant.

> Tohmo has no public API. This integration uses the internal API of the customer portal `app.go-tohmo.com`, so any change to the website may break it.

## Installation

**HACS**:

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lobodart&repository=hass-tohmo&category=integration)

or *HACS → ⋮ → Custom repositories*, add `https://github.com/lobodart/hass-tohmo` (type *Integration*). Install **Tohmo**, then restart Home Assistant.

**Manual**: copy `custom_components/tohmo` into the `config/custom_components/` folder of Home Assistant, then restart.

Then go to *Settings → Devices & services → Add integration → Tohmo* and enter the email and password of your Tohmo account.

Requires Home Assistant 2026.10 or later. The UI is available in English and French.

## Entities

Per charging station:

| Entity | Description |
|---|---|
| Total energy | kWh of all charging sessions (`total_increasing`) |
| Total cost | sum of billed costs (older sessions without a cost count as 0) |
| Last session energy / cost / start / end / duration | latest session, with its tariff (peak/off-peak, price per kWh) as attributes |
| Charging mode | `eco` or `continuous`, with the eco schedule as an attribute |
| Off-peak hours | `on` during an eco window, switches at the exact time (usable in automations) |
| Next off-peak | start of the next eco window (timestamp) |
| Off-peak N start / end | time (`HH:MM`) of each eco window. Created automatically: if Tohmo adds a window, its entities appear; if a window is removed, its entities become unavailable |
| Charging | a session is in progress |
| Last heartbeat | diagnostic, disabled by default |
| Monthly subscription | subscription fee, with the offer and start date as attributes |
| Peak / off-peak price | price per kWh of the contract |

For the account: energy, cost and number of sessions of the current billing period; **Last invoice** (amount, with dates and status as attributes); **Pending invoices** (count, with the total amount as an attribute).

The station device uses the name given to the station in the Tohmo app.

### History and Energy dashboard

The integration also imports the whole charging history as **hourly statistics**:

- **Tohmo &lt;station&gt; energy** (`tohmo:energy_<station number>`, e.g. `tohmo:energy_frabcp1234`): kWh per hour since the station was installed;
- **Tohmo &lt;station&gt; cost** (`tohmo:cost_<station number>`): cost per hour, only for sessions billed per kWh (flat-rate offers have no cost per session).

Each session is spread over the hours it actually lasted, at constant power. New sessions are added as soon as Tohmo publishes them (when they end), on their real hours.

For the Energy dashboard: *Settings → Dashboards → Energy → Individual devices* → pick **Tohmo &lt;station&gt; energy**. Do not also add the *Total energy* sensor, or the consumption would be counted twice.

The Energy dashboard shows no cost for individual devices: use the cost statistic in a *Statistics graph* card.

Invoice amounts are not imported as such: invoices use a rolling period (a session may be billed the following month), while statistics follow the real charging time. Totals match session by session.

## How it works

- Refreshes every 10 minutes. On startup the whole session history is loaded; afterwards only recent sessions are read again.
- The Tohmo session lasts 7 days: when it expires (HTTP 401), the integration signs in again with the stored credentials.
- If Tohmo rejects the password (changed in the meantime), Home Assistant asks to re-authenticate.
- The ID of the website sign-in action changes with each deployment: it is found again automatically in the sign-in page JavaScript.
- Eco windows and session times returned by Tohmo are in Paris time (session times are even wrongly tagged as UTC): the integration interprets them in `Europe/Paris`.

API details: [API_NOTES.md](API_NOTES.md).

## Tests

```sh
docker build -t tohmo-test - <<'EOF'
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ libturbojpeg0 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest-homeassistant-custom-component==0.13.375
WORKDIR /repo
EOF
docker run --rm -v "$PWD":/repo tohmo-test python -m pytest -q -p no:cacheprovider
```

## Disclaimer

This project is not affiliated with or endorsed by Tohmo or Zeplug. Use it at your own risk.
