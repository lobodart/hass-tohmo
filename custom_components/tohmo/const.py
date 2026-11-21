"""Constants for the Tohmo integration."""

from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "tohmo"
LOGGER = logging.getLogger(__package__)

BASE_URL = "https://app.go-tohmo.com"
TENANT_SLUG = "tohmo"
# Eco schedules and session times are French local times.
TOHMO_TIME_ZONE = "Europe/Paris"

UPDATE_INTERVAL = timedelta(minutes=10)
