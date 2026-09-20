"""Protection diagnostics entity for the dashboard.

One entity per integration (not per device):
``binary_sensor.<name>_protection`` - "on" means attention needed: a
command was blocked, or the device has been unreachable (3+ failed
commands in a row after retries). Attributes carry the last blocked
command, failure/success details and running totals.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PROTECTION_ENTITY
from .issues import get_protection_status

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities,
) -> None:
    """Add the protection diagnostics entity (single, integration-wide)."""
    status = get_protection_status(hass)
    if status is None:  # setup order guard; reporter is installed first
        return
    async_add_entities([B01ProtectionBinarySensor(status)])


class B01ProtectionBinarySensor(BinarySensorEntity):
    """On = protection fired (blocked command or unreachable device)."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Protection"
    _attr_unique_id = f"{DOMAIN}_{PROTECTION_ENTITY}"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, status) -> None:
        self._status = status

    # ------------------------------------------------- lifecycle

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._status.register(self)

    async def async_will_remove_from_hass(self) -> None:
        self._status.unregister(self)
        await super().async_will_remove_from_hass()

    # ------------------------------------------------- state

    @property
    def is_on(self) -> bool:
        attrs = self._status.attributes()
        return bool(
            attrs["last_blocked_command"] is not None
            or attrs["device_unreachable"]
        )

    @property
    def extra_state_attributes(self) -> dict:
        return self._status.attributes()
