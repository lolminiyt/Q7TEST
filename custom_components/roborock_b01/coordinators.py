"""Per-device coordinators for the standalone Roborock B01 integration.

Q7: polls ``GET_PROP`` (status, wind, battery, consumables, ...) on a
timer; the map itself is push-driven via the library's listener.
Q10: fully push-driven - the coordinator only sends a read-only
REQUEST_DPS kick; entities listen to the traits directly.

Compatibility: the camera, area-mapping keeper and services all use
the ``.api`` / ``.duid`` / ``.data`` / ``.async_refresh()`` surface,
which these keep from the previous (piggyback) architecture.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from roborock.devices.device import RoborockDevice
from roborock.exceptions import RoborockException
from roborock.roborock_message import RoborockB01Props

from .const import DOMAIN, Q7_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class B01Q7Coordinator(DataUpdateCoordinator):
    """Polls Q7 properties (status/wind/battery/consumables)."""

    def __init__(self, hass: HomeAssistant, device: RoborockDevice) -> None:
        self.device = device
        self.api = device.b01_q7_properties
        self.duid = device.duid
        self.request_protocols = [
            RoborockB01Props.STATUS,
            RoborockB01Props.MAIN_BRUSH,
            RoborockB01Props.SIDE_BRUSH,
            RoborockB01Props.DUST_BAG_USED,
            RoborockB01Props.MOP_LIFE,
            RoborockB01Props.MAIN_SENSOR,
            RoborockB01Props.CLEANING_TIME,
            RoborockB01Props.REAL_CLEAN_TIME,
            RoborockB01Props.HYPA,
            RoborockB01Props.WIND,
            RoborockB01Props.WATER,
            RoborockB01Props.MODE,
            RoborockB01Props.CLEAN_PATH_PREFERENCE,
            RoborockB01Props.QUANTITY,
        ]
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device.duid}",
            update_interval=timedelta(seconds=Q7_POLL_INTERVAL),
        )

    async def _async_update_data(self):
        try:
            data = await self.api.query_values(self.request_protocols)
        except RoborockException as err:
            raise UpdateFailed(f"Q7 update failed: {err}") from err
        if data is None:
            raise UpdateFailed("Q7 returned no properties")
        return data


class B01Q10Coordinator(DataUpdateCoordinator):
    """Kick-only coordinator for the push-driven Q10.

    The Q10 streams status/map over MQTT on its own;
    ``_async_update_data`` sends a read-only REQUEST_DPS to solicit a
    status push and returns immediately. Entities read traits directly
    and subscribe to trait update listeners.
    """

    def __init__(self, hass: HomeAssistant, device: RoborockDevice) -> None:
        self.device = device
        self.api = device.b01_q10_properties
        self.duid = device.duid
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device.duid}",
            update_interval=timedelta(seconds=Q7_POLL_INTERVAL),
        )

    async def _async_update_data(self):
        try:
            await self.api.refresh()
        except RoborockException as err:
            raise UpdateFailed(f"Q10 status kick failed: {err}") from err
