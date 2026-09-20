"""Vacuum control patches for HA core RoborockQ7Vacuum / RoborockQ10Vacuum.

Ground truth for every API used here (python-roborock, verified against
library source and its test suite, plus the HA core roborock component):

Q7 (pv=B01) via ``coordinator.api`` = ``Q7PropertiesApi``:
- ``clean_segments(room_ids)``  -> ``service.set_room_clean``
  {clean_type: 1, ctrl_value: 1, room_ids: [...]}          (room clean)
- ``start_clean()`` / ``pause_clean()`` / ``stop_clean()``
- ``return_to_dock()`` / ``find_me()`` / ``set_fan_speed(...)``
- ``map`` (MapTrait): ``current_map_id`` after ``refresh()``
  (``service.get_map_list``)
- ``map_content`` (MapContentTrait): ``refresh()``,
  ``image_content`` (PNG bytes), ``map_data`` (vacuum_map_parser_base
  MapData: ``vacuum_position`` {x, y, a}, ``rooms`` {id: Room},
  ``additional_parameters['room_names']`` {id: str}) and
  ``add_update_listener(cb)`` for unsolicited live frames.
- The device itself streams live map frames while cleaning; the
  subscription is started by the library when the device connects
  (``RoborockDevice.connect`` -> ``b01_q7_properties.start()``).

Q10 (pv=B01) via ``coordinator.api`` = ``Q10PropertiesApi``:
- ``map`` (MapContentTrait): ``image_content``, ``rooms`` (list of
  ``Q10Room(id, name)`` - ids are the segment ids), ``robot_position`` -
  all push-driven; ``refresh()`` sends the read-only REQUEST_DPS.
- ``maps`` (MapsTrait): ``current_map_id`` (str) after ``refresh()``.
- Control (goto/zone/segments) is fully implemented by HA core's
  ``RoborockQ10Vacuum`` and is NOT patched here.

``SCWindMapping`` (Q7 fan levels) is a ``RoborockModeEnum`` with
``.keys()`` and ``.from_value(name)``; core already wires it up.

HA core (verified from source) stubs ``get_maps`` / position on the Q7
and gives the Q7 no ``clean_segments``. This module patches exactly
those gaps onto the core classes (idempotent; the classes are
singletons in sys.modules) and flips the Q7's feature flags.

Deliberately NOT implemented: Q7 point/zone cleaning. The library has
no wrapper for it and no verified wire payload shape exists (checked
upstream main and community captures); per this project's no-guesses
rule, ``set_vacuum_goto_position`` / ``set_vacuum_zoned_cleaning`` are
left unimplemented on the Q7 (calls fail with core's standard
"not supported" error).
"""

from __future__ import annotations

import asyncio
import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Time to wait for the Q10 push stream to deliver the room list after
# kicking it with a read-only refresh.
_ROOMS_WAIT_TIMEOUT = 30  # seconds
_ROOMS_POLL_INTERVAL = 1.0  # seconds


def _q7_room_names(api) -> dict[int, str]:
    """Best-effort room id -> name map for a Q7 from parsed map content."""
    rooms: dict[int, str] = {}
    map_data = api.map_content.map_data
    if map_data is None:
        return rooms
    named = getattr(map_data, "rooms", None) or {}
    for room_id, room in named.items():
        try:
            rooms[int(room_id)] = getattr(room, "name", None) or f"Room {room_id}"
        except (TypeError, ValueError):
            continue
    if rooms:
        return rooms
    raw_names = (getattr(map_data, "additional_parameters", None) or {}).get(
        "room_names"
    )
    for room_id, name in (raw_names or {}).items():
        try:
            rooms[int(room_id)] = str(name)
        except (TypeError, ValueError):
            continue
    return rooms


def patch_b01_vacuum_classes() -> list[str]:
    """Patch core B01 vacuum classes. Returns the applied patch names."""
    from homeassistant.components.roborock.vacuum import (
        RoborockQ7Vacuum,
        RoborockQ10Vacuum,
    )
    from homeassistant.components.vacuum import Segment, VacuumEntityFeature
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
    from roborock.exceptions import RoborockException

    applied: list[str] = []

    # ============================== Q7 ==============================

    async def q7_get_maps(self) -> dict:
        """Current map + rooms in the V1 get_maps response shape."""
        api = self.coordinator.api
        try:
            await api.map.refresh()
            await api.map_content.refresh()
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="map_failure",
            ) from err
        rooms = _q7_room_names(api)
        map_id = api.map.current_map_id
        map_name = f"Map {map_id}" if map_id is not None else "Map"
        return {
            "maps": [
                {
                    "flag": map_id,
                    "name": map_name,
                    "rooms": rooms,
                }
            ]
        }

    async def q7_async_get_segments(self) -> list[Segment]:
        """Rooms as segments for the room-cleaning UI (manual flows)."""
        response = await q7_get_maps(self)
        maps = response["maps"]
        if not maps:
            return []
        first = maps[0]
        group = first["name"]
        return [
            Segment(id=str(room_id), name=name, group=group)
            for room_id, name in first["rooms"].items()
        ]

    async def q7_async_clean_segments(
        self, segment_ids: list[str], **kwargs
    ) -> None:
        """Clean rooms by id via the library's SET_ROOM_CLEAN wrapper."""
        try:
            ids = [int(str(seg).strip()) for seg in segment_ids]
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_segment",
                translation_placeholders={"segments": ", ".join(segment_ids)},
            ) from err
        try:
            await self.coordinator.api.clean_segments(ids)
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": "clean_segments"},
            ) from err

    async def q7_get_vacuum_current_position(self) -> dict:
        """Robot x/y (map pixel coordinates) from the parsed live map."""
        api = self.coordinator.api
        try:
            await api.map.refresh()
            await api.map_content.refresh()
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="map_failure",
            ) from err
        map_data = api.map_content.map_data
        pos = map_data.vacuum_position if map_data else None
        if pos is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="position_not_found",
            )
        return {"x": round(pos.x), "y": round(pos.y)}

    RoborockQ7Vacuum.get_maps = q7_get_maps
    RoborockQ7Vacuum.async_get_segments = q7_async_get_segments
    RoborockQ7Vacuum.async_clean_segments = q7_async_clean_segments
    RoborockQ7Vacuum.get_vacuum_current_position = q7_get_vacuum_current_position
    applied += [
        "Q7.get_maps",
        "Q7.get_segments",
        "Q7.clean_segments",
        "Q7.position",
    ]

    # CLEAN_AREA is registered against the feature flag. Core defines
    # supported_features as a (non-data) cached_property, so an entity
    # created before this patch - or any already-cached instance value -
    # would keep the old flags forever. A class-level property is a data
    # descriptor: it wins over the cached_property AND any stale
    # per-instance cache, so every Q7 entity (present or future)
    # advertises room cleaning regardless of integration setup order.
    if not getattr(RoborockQ7Vacuum, "_b01_clean_area", False):
        base_features = getattr(
            RoborockQ7Vacuum, "_attr_supported_features", VacuumEntityFeature(0)
        )
        q7_features = base_features | VacuumEntityFeature.CLEAN_AREA
        RoborockQ7Vacuum._attr_supported_features = q7_features

        def _supported_features(self):
            """Patched by roborock_b01: adds CLEAN_AREA to the Q7.

            Reads the (already OR'd) class attribute so per-instance
            overrides keep working; being a data descriptor it also
            defeats any stale cached_property value computed before this
            patch loaded.
            """
            return self._attr_supported_features

        RoborockQ7Vacuum.supported_features = property(_supported_features)
        RoborockQ7Vacuum._b01_clean_area = True
    applied.append("Q7.CLEAN_AREA")

    # ============================== Q10 ==============================

    async def q10_get_maps(self) -> dict:
        """Current map + rooms from the push-driven Q10 map trait."""
        api = self.coordinator.api
        try:
            await api.map.refresh()
            await api.maps.refresh()
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="map_failure",
            ) from err
        # Room names ride in with the next map push (map trait is
        # push-driven; the refreshes above only kick the stream).
        deadline = asyncio.get_running_loop().time() + _ROOMS_WAIT_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            if api.map.rooms:
                break
            await asyncio.sleep(_ROOMS_POLL_INTERVAL)
        rooms = {
            room.id: room.name or f"Room {room.id}" for room in api.map.rooms
        }
        map_id = api.maps.current_map_id
        return {
            "maps": [
                {
                    "flag": map_id,
                    "name": f"Map {map_id}" if map_id is not None else "Map",
                    "rooms": rooms,
                }
            ]
        }

    RoborockQ10Vacuum.get_maps = q10_get_maps
    applied.append("Q10.get_maps")

    # Core already supports CLEAN_AREA on the Q10; asserted here so a
    # future core change cannot silently drop it out from under our
    # service's capability check.
    RoborockQ10Vacuum._attr_supported_features = (
        RoborockQ10Vacuum._attr_supported_features | VacuumEntityFeature.CLEAN_AREA
    )
    applied.append("Q10.CLEAN_AREA")

    return applied
