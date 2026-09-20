"""Live map cameras for B01 Q7/Q10 devices.

Directly implements the DEVICES.md B01 pattern:

- Q7 streams full map frames on its own while cleaning. We call
  api.start() (subscribes to map pushes) and register
  api.map_content.add_update_listener(cb), then read image_content
  when notified. No polling, no heartbeat.
- Q10 composes its map from map/trace/DPS streams; we listen on
  api.map updates and request pushes via api.map.refresh().

Entities attach to the existing core Roborock device
(identifiers {("roborock", duid)}), so no new device is created.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

_RESCAN_DELAY = timedelta(seconds=60)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Create map cameras for every B01 coordinator, present and future."""
    known: set[str] = set()

    def _add(coord, is_q7: bool) -> None:
        if coord.duid in known:
            return
        known.add(coord.duid)
        async_add_entities([B01MapCamera(coord, is_q7)])

    @callback
    def _late(coord) -> None:
        from homeassistant.components.roborock.coordinator import (
            RoborockB01Q7UpdateCoordinator,
        )

        _add(coord, isinstance(coord, RoborockB01Q7UpdateCoordinator))

    def _scan(_now=None) -> None:
        for entry in hass.config_entries.async_entries("roborock"):
            coordinators = getattr(entry, "runtime_data", None)
            if coordinators is None:
                continue
            for coord in coordinators.b01_q7:
                _add(coord, True)
            for coord in coordinators.b01_q10:
                _add(coord, False)
            entry.async_on_unload(
                async_dispatcher_connect(
                    hass,
                    f"roborock_coordinator_added_{entry.entry_id}",
                    _late,
                )
            )

    _scan()
    # Re-scan once in case the core entry finished after us.
    async_call_later(hass, _RESCAN_DELAY, _scan)


class B01MapCamera(Camera):
    """Push-driven live map image for a B01 vacuum."""

    _attr_content_type = "image/png"
    _attr_has_entity_name = True
    _attr_name = "Map"

    def __init__(self, coordinator, is_q7: bool) -> None:
        """Initialize."""
        super().__init__()
        self.coordinator = coordinator
        self._is_q7 = is_q7
        self._attr_unique_id = f"{coordinator.duid}_b01_map"
        self._attr_device_info = DeviceInfo(
            identifiers={("roborock", coordinator.duid)}
        )
        self._image: bytes | None = None
        self._unsub_push = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to live map pushes and cache the first frame."""
        await super().async_added_to_hass()
        api = self.coordinator.api
        # Q7: subscribe to unsolicited SCMap frames (DEVICES.md pattern).
        # Guarded: DeviceManager may already have started it.
        start = getattr(api, "start", None)
        if callable(start):
            try:
                await start()
            except Exception as err:  # pragma: no cover
                _LOGGER.debug("roborock_b01: map push subscribe failed: %s", err)
        await self._refresh_image()
        try:
            trait = api.map_content if self._is_q7 else api.map
            self._unsub_push = trait.add_update_listener(self._on_push)
        except Exception as err:  # pragma: no cover
            _LOGGER.debug("roborock_b01: push listener failed: %s", err)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from pushes."""
        if self._unsub_push is not None:
            try:
                self._unsub_push()
            except Exception:  # pragma: no cover
                pass
            self._unsub_push = None
        await super().async_will_remove_from_hass()

    @callback
    def _on_push(self) -> None:
        """Cache the pushed frame and update state."""
        try:
            api = self.coordinator.api
            trait = api.map_content if self._is_q7 else api.map
            self._image = trait.image_content
        except Exception:  # pragma: no cover
            pass
        self.async_write_ha_state()

    async def _refresh_image(self) -> None:
        """Fetch one frame on demand (best effort, MQTT-only per B01)."""
        from roborock.exceptions import RoborockException

        api = self.coordinator.api
        try:
            if self._is_q7:
                await api.map.refresh()
                await api.map_content.refresh()
                self._image = api.map_content.image_content
            else:
                await api.maps.refresh()
                await api.map.refresh()
                self._image = api.map.image_content
        except RoborockException as err:
            _LOGGER.debug("roborock_b01: map refresh failed: %s", err)
        except Exception as err:  # pragma: no cover
            _LOGGER.debug("roborock_b01: unexpected map error: %s", err)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest cached map frame, fetching once if empty."""
        if self._image is None:
            await self._refresh_image()
        return self._image
