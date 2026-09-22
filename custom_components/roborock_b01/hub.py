"""Per-entry hub: session + coordinators registry for standalone operation.

Owns what the official Roborock integration used to own for us:
- the cloud login (session.py) and device connections,
- one coordinator per B01 device,
- a hass.data registry that the camera/binary_sensor/keeper platforms
  read instead of scanning the official integration's entries.

The registry shape is deliberately the old architecture's surface:
``hass.data[DOMAIN]["coordinators"][duid] -> coordinator`` with
``.api`` / ``.duid`` / ``.data`` / ``.async_refresh()``, so the camera,
area-mapping keeper, services and tests stay drop-in compatible.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinators import B01Q7Coordinator, B01Q10Coordinator

_LOGGER = logging.getLogger(__name__)


def get_coordinators(hass: HomeAssistant, entry: ConfigEntry) -> list:
    """All coordinators for this entry (created at setup)."""
    return list(hass.data.setdefault(DOMAIN, {}).get("coordinators", {}).values())


async def async_setup_hub(hass: HomeAssistant, entry: ConfigEntry) -> list:
    """Log in, connect devices, create coordinators. Returns them."""
    from .session import async_create_session

    session = await async_create_session(hass, entry)
    devices = await session.b01_devices()
    if not devices:
        await session.close()
        raise _NoB01Devices("No B01 (Q7/Q10) vacuums found in this Roborock account")

    data = hass.data.setdefault(DOMAIN, {})
    data.setdefault("sessions", {})[entry.entry_id] = session
    coordinators: dict = data.setdefault("coordinators", {})

    created = []
    for device in devices:
        if device.b01_q7_properties is not None:
            coordinator = B01Q7Coordinator(hass, device)
        elif device.b01_q10_properties is not None:
            coordinator = B01Q10Coordinator(hass, device)
        else:  # pragma: no cover - filtered by b01_devices()
            continue
        await coordinator.async_config_entry_first_refresh()
        coordinators[device.duid] = coordinator
        created.append(coordinator)
        _LOGGER.info(
            "roborock_b01: coordinator ready for %s (%s)",
            device.name,
            device.product.model,
        )
    return created


async def async_unload_hub(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Close this entry's session; drop its coordinators."""
    data = hass.data.get(DOMAIN, {})
    sessions = data.get("sessions", {})
    session = sessions.pop(entry.entry_id, None)
    if session is not None:
        await session.close()
    # Coordinators are per-session; if this was the last session, drop
    # them so platforms re-creating on the next setup start clean.
    if not sessions:
        data.pop("coordinators", None)


class _NoB01Devices(Exception):
    """Raised when the account holds no B01 devices."""
