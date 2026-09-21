"""Roborock B01 (Q7 / Q10) full support.

Fills the map / room-control gaps that Home Assistant core leaves for
B01-protocol (pv=B01) vacuums: Q7 BF/TF/M5/L5 and Q10 series.

Everything reuses the already-authenticated session of the official
Roborock integration (its coordinators expose the python-roborock
``Q7PropertiesApi`` / ``Q10PropertiesApi``). No extra login.

What is added on top of core (all implemented with library-verified
commands, see vacuum.py):

- ``camera.<name>_map`` - live, push-driven map (no polling)
- Room cleaning on Q7 (core has no ``clean_segments`` for the Q7 class)
- ``roborock.get_maps`` / ``get_vacuum_current_position`` for the Q7
- ``roborock.get_maps`` for the Q10 (its map is push-driven; core only
  exposes the position; goto/zone on the Q10 is core-provided)
- A ``roborock_b01.clean_segment`` service on top of the segment-repair
  flow, which needs a platform-registered service

Setup: UI (config entry, preferred) or ``roborock_b01:`` YAML.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): {}}, extra=vol.ALLOW_EXTRA)


@callback
def _async_apply_once(hass: HomeAssistant) -> bool:
    """Apply vacuum patches and register services (idempotent)."""
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get("_b01_patched"):
        from .issues import async_setup_issue_reporter
        from .vacuum import patch_b01_vacuum_classes

        applied = patch_b01_vacuum_classes()
        if applied:
            _LOGGER.info("roborock_b01: patches applied: %s", applied)
        else:
            # Old HA core: refuse setup (the guard logged the reason);
            # not marking patched means a retry/restart after upgrading
            # HA re-applies the patches.
            return False
        data["_b01_unsub_issues"] = async_setup_issue_reporter(hass)
        data["_b01_patched"] = True
        return True
    return False


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up via YAML (skipped when a UI entry exists)."""
    if DOMAIN not in config:
        return True
    if hass.config_entries.async_entries(DOMAIN):
        return True
    from .area_mapping import async_setup_area_mapping_keeper
    from .services import async_register_services

    if not _async_apply_once(hass):
        # Old HA core (guard logged the reason): fail the YAML setup
        # instead of running a half-featured integration.
        return False
    async_register_services(hass)
    async_setup_area_mapping_keeper(hass, None)
    await async_load_platform(hass, "camera", DOMAIN, {}, config)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up via UI config entry."""
    from .area_mapping import async_setup_area_mapping_keeper
    from .services import async_register_services

    if not _async_apply_once(hass):
        # Old HA core (guard logged the reason): mark the entry failed
        # so the UI shows it, instead of a silently half-featured setup.
        return False
    async_register_services(hass)
    # Reconcile the segment-to-area mapping (backup / restore / auto-map)
    # whenever a B01 coordinator shows up, now or in the future.
    async_setup_area_mapping_keeper(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload UI config entry (removes map cameras)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
