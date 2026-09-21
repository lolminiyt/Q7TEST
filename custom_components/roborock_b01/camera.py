"""Live map cameras for B01 Q7/Q10 devices.

Map sources (python-roborock, verified):

- Q7: the device streams full SCMap frames on its own while cleaning.
  The subscription is started by the library when the device connects
  (``RoborockDevice.connect`` -> ``b01_q7_properties.start()``). We only
  register ``api.map_content.add_update_listener(cb)`` and read
  ``image_content`` when notified. On demand, fetch one frame with
  ``map.refresh()`` (GET_MAP_LIST) + ``map_content.refresh()``
  (UPLOAD_BY_MAPID for the current map id).
- Q10: the map is composed from pushed map/trace packets (stream
  started by the library at connect). We listen on ``api.map``, read
  ``image_content``, and kick the stream with the read-only
  ``map.refresh()`` (REQUEST_DPS) when we need a first frame.

Entities attach to the existing core Roborock device
(``identifiers={("roborock", duid)}``) - no extra device is created.
"""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import LATE_SCAN_DELAY

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create map cameras for the UI config entry (proper unload support)."""
    _async_setup_b01_cameras(hass, async_add_entities, entry)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Create map cameras for YAML setup (legacy fallback)."""
    _async_setup_b01_cameras(hass, async_add_entities, None)


def _async_setup_b01_cameras(
    hass: HomeAssistant,
    async_add_entities: AddEntitiesCallback,
    entry: ConfigEntry | None,
) -> None:
    """Create a map camera for every B01 coordinator, present and future."""
    known: set[str] = set()
    watched_entries: set[str] = set()

    def _add(coord) -> None:
        if coord.duid in known:
            return
        known.add(coord.duid)
        async_add_entities([B01MapCamera(coord)])

    @callback
    def _late(coord) -> None:
        """Handle a coordinator added after our setup ran."""
        _add(coord)

    @callback
    def _scan(_now=None) -> None:
        """Add cameras for all known B01 coordinators."""
        for rob_entry in hass.config_entries.async_entries("roborock"):
            # One dispatcher subscription per core entry: _scan runs
            # twice (immediate + delayed rescan), so without this guard
            # each entry would end up with two live _late listeners.
            if rob_entry.entry_id in watched_entries:
                continue
            watched_entries.add(rob_entry.entry_id)
            remove_listener = async_dispatcher_connect(
                hass,
                f"roborock_coordinator_added_{rob_entry.entry_id}",
                _late,
            )
            if entry is not None:
                # UI mode: listener dies with our config entry.
                entry.async_on_unload(remove_listener)
            # YAML (legacy) mode: keep it until HA stops - same
            # tradeoff the coordinators watcher makes.
            coordinators = getattr(rob_entry, "runtime_data", None)
            if coordinators is None:
                continue
            # Old HA cores have no B01 coordinator lists; the vacuum-patch
            # guard already logged that room/map features are disabled.
            for coord in list(getattr(coordinators, "b01_q7", ()) or ()):
                _add(coord)
            for coord in list(getattr(coordinators, "b01_q10", ()) or ()):
                _add(coord)

    _scan()
    # Re-scan once in case the core entry finished after us.
    remove_rescan = async_call_later(hass, LATE_SCAN_DELAY, _scan)
    if entry is not None:
        entry.async_on_unload(remove_rescan)


class B01MapCamera(Camera):
    """Push-driven live map image for a B01 vacuum."""

    _attr_content_type = "image/png"
    _attr_has_entity_name = True
    _attr_name = "Map"
    _attr_should_poll = False

    def __init__(self, coordinator) -> None:
        """Initialize from the core coordinator (Q7 or Q10)."""
        super().__init__()
        self.coordinator = coordinator
        self._api = coordinator.api
        self._is_q7 = hasattr(self._api, "map_content")
        self._attr_unique_id = f"{coordinator.duid}_b01_map"
        self._attr_device_info = DeviceInfo(
            identifiers={("roborock", coordinator.duid)}
        )
        self._image: bytes | None = None
        self._unsub_push = None

    def _map_trait(self):
        """The trait that carries the rendered PNG."""
        return self._api.map_content if self._is_q7 else self._api.map

    async def async_added_to_hass(self) -> None:
        """Subscribe to live map pushes and fetch an initial frame."""
        await super().async_added_to_hass()
        # The library already subscribed the device's push stream when it
        # connected (device.connect() starts B01 traits); DO NOT call
        # api.start() again - for Q10 that would spawn a second subscribe
        # task. Just listen for trait updates.
        try:
            self._unsub_push = self._map_trait().add_update_listener(self._on_push)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("roborock_b01: push listener failed: %s", err)
        if self._image is None:
            await self._refresh_image()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from pushes."""
        if self._unsub_push is not None:
            try:
                self._unsub_push()
            except Exception as err:
                _LOGGER.debug("roborock_b01: push unsubscribe failed: %s", err)
            self._unsub_push = None
        await super().async_will_remove_from_hass()

    @callback
    def _on_push(self) -> None:
        """Cache the pushed frame and update state."""
        try:
            self._image = self._map_trait().image_content
        except Exception:  # pragma: no cover - defensive
            return
        self.async_write_ha_state()

    async def _refresh_image(self) -> None:
        """Fetch one frame on demand (MQTT-only per the B01 protocol)."""
        from roborock.exceptions import RoborockException

        try:
            if self._is_q7:
                # GET_MAP_LIST (current map id) then UPLOAD_BY_MAPID.
                await self._api.map.refresh()
                await self._api.map_content.refresh()
            else:
                # Read-only REQUEST_DPS kick; frames arrive via pushes.
                await self._api.map.refresh()
                await self._api.maps.refresh()
            self._image = self._map_trait().image_content
        except RoborockException as err:
            _LOGGER.debug("roborock_b01: map refresh failed: %s", err)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("roborock_b01: unexpected map error: %s", err)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest cached map frame, fetching once if empty."""
        if self._image is None:
            await self._refresh_image()
        return self._image
