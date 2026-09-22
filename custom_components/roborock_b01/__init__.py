"""Standalone Roborock B01 (Q7 / Q10) full support.

Owns its own Roborock cloud session (login + MQTT devices via the
python-roborock library) - no dependency on the official Roborock
integration. On top of the connected devices it provides:

- owned vacuum entities with full controls (start/pause/stop, fan
  speed, locate, room cleaning, Q10 goto/zone), with the map/room
  features the official integration's Q7 lacks;
- ``camera.<name>_map`` - live, push-driven map;
- the area-mapping keeper (backup / restore / auto-map / stale trim);
- the Q7 map-protection wire guard and the bounded-retry failsafe;
- ``roborock_b01.clean_segment`` / ``clean_settings`` services.

Setup: UI only (config flow logs in with your Roborock account).
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Log in, connect devices, create coordinators, forward platforms."""
    from .hub import _NoB01Devices, async_setup_hub
    from .issues import async_setup_issue_reporter
    from .services import async_register_services
    from .vacuum import install_q7_wire_guard

    data = hass.data.setdefault(DOMAIN, {})
    # Map protection is process-wide and idempotent; install once.
    if not data.get("_b01_wire_guard"):
        guard = install_q7_wire_guard()
        _LOGGER.info("roborock_b01: %s installed", guard)
        data["_b01_wire_guard"] = True
    if "_b01_unsub_issues" not in data:
        data["_b01_unsub_issues"] = async_setup_issue_reporter(hass)

    try:
        coordinators = await async_setup_hub(hass, entry)
    except _NoB01Devices as err:
        raise ConfigEntryNotReady(str(err)) from err

    if not data.get("_b01_services"):
        async_register_services(hass)
        data["_b01_services"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinators:
        # Give the area-mapping keeper a late pass too (entities may
        # register after platform setup).
        from .area_mapping import async_setup_area_mapping_keeper

        async_setup_area_mapping_keeper(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload: close the session, keep the process-wide guard."""
    from .hub import async_unload_hub

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await async_unload_hub(hass, entry)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Final cleanup when the entry is deleted (guard stays: idempotent)."""
    from .hub import async_unload_hub

    await async_unload_hub(hass, entry)
