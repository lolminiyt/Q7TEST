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
singletons in sys.modules) and adds the Q7's CLEAN_AREA feature flag
at read time (robust to all of core's feature plumbing shapes).

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
from .failsafe import assert_q7_command_allowed, async_with_retry

_LOGGER = logging.getLogger(__name__)

# Time to wait for the Q10 push stream to deliver the room list after
# kicking it with a read-only refresh.
_ROOMS_WAIT_TIMEOUT = 30  # seconds
_ROOMS_POLL_INTERVAL = 1.0  # seconds


async def _q7_current_room_ids(api) -> dict[int, str]:
    """The room ids the robot can actually clean right now (id -> name).

    Fetched from the device map (GET_MAP_LIST + UPLOAD_BY_MAPID) - the
    same id space the SET_ROOM_CLEAN command expects. Raises
    ``RoborockException`` when the device/map is unreachable, so a
    transient failure can never look like "unknown room".
    """
    await async_with_retry("get_map_list", api.map.refresh)
    await async_with_retry("upload_by_mapid", api.map_content.refresh)
    return _q7_room_names(api)


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
    """Patch core B01 vacuum classes. Returns the applied patch names.

    Returns an empty list (and logs one clear error) when the running
    HA core predates the B01 vacuum classes (2026.9); setup then fails
    fast with that reason.
    """
    try:
        from homeassistant.components.roborock.vacuum import (
            RoborockQ7Vacuum,
            RoborockQ10Vacuum,
        )
        from homeassistant.components.vacuum import Segment, VacuumEntityFeature
        from homeassistant.exceptions import (
            HomeAssistantError,
            ServiceValidationError,
        )
        from roborock.data.b01_q7.b01_q7_code_mappings import (
            CleanTaskTypeMapping,
            SCDeviceCleanParam,
            SCWindMapping,
        )
        from roborock.exceptions import RoborockException
    except ImportError as err:
        _LOGGER.error(
            "roborock_b01: this Home Assistant does not provide the B01 "
            "vacuum classes (RoborockQ7Vacuum/RoborockQ10Vacuum landed in "
            "HA 2026.9) or the pinned python-roborock 7.8.1: %s. The "
            "integration entry will show as failed - upgrade Home "
            "Assistant to 2026.9+ and reload.",
            err,
        )
        return []

    applied: list[str] = []

    # ============================== Q7 ==============================

    async def q7_get_maps(self) -> dict:
        """Current map + rooms in the V1 get_maps response shape."""
        api = self.coordinator.api
        try:
            await async_with_retry("get_map_list", api.map.refresh)
            await async_with_retry("upload_by_mapid", api.map_content.refresh)
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

    async def q7_async_clean_segments(self, segment_ids: list[str], **kwargs) -> None:
        """Clean rooms by id via the library's SET_ROOM_CLEAN wrapper.

        Failsafe: empty or malformed ids are refused before anything
        goes on the wire, and the (atomic) device command is retried
        on transient failures - never half-applied.
        """
        try:
            ids = [int(str(seg).strip()) for seg in segment_ids]
        except (TypeError, ValueError) as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_segment",
                translation_placeholders={"segments": str(segment_ids)},
            ) from err
        if not ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_segment",
                translation_placeholders={"segments": "(empty)"},
            )
        # Failsafe: the device starts a FULL-HOUSE clean when SET_ROOM_CLEAN
        # carries room ids it does not know (e.g. dashboard placeholders or
        # a stale segment->area mapping). Verify against the robot's current
        # room list before anything goes on the wire.
        try:
            known = await _q7_current_room_ids(self.coordinator.api)
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": "get_map_list"},
            ) from err
        unknown = [room_id for room_id in ids if room_id not in known]
        if unknown or not known:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_segment",
                translation_placeholders={
                    "segments": ", ".join(str(room_id) for room_id in unknown or ids),
                    "valid": ", ".join(
                        f"{room_id}={name}" for room_id, name in known.items()
                    )
                    or "none (no map data on the robot)",
                },
            )
        # Debug-log the exact wire payload before it goes out: this is
        # the string to compare against a Roborock-app MQTT capture when
        # a room clean misbehaves on-device (see TESTING.md).
        _LOGGER.debug(
            "roborock_b01: %s clean_segments -> service.set_room_clean "
            "{clean_type: %d, ctrl_value: %d, room_ids: %s}",
            getattr(self, "entity_id", self.__class__.__name__),
            CleanTaskTypeMapping.ROOM.code,
            SCDeviceCleanParam.START.code,
            ids,
        )
        try:
            await async_with_retry(
                "clean_segments", self.coordinator.api.clean_segments, ids
            )
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": "clean_segments"},
            ) from err
        # Keep the UI (fan speed, activity, ...) current instead of waiting
        # for the next one-minute poll.
        await self.coordinator.async_refresh()

    async def q7_get_vacuum_current_position(self) -> dict:
        """Robot x/y (map pixel coordinates) from the parsed live map."""
        api = self.coordinator.api
        try:
            await _q7_current_room_ids(api)
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

    async def q7_async_set_fan_speed(self, fan_speed: str, **kwargs) -> None:
        """Core's Q7 setter + an immediate UI refresh.

        Core validates against SCWindMapping and sends via the library;
        the only gap was the stale UI (fan_speed reads
        coordinator.data.wind_name, updated by the one-minute poll).
        """
        try:
            fan_speed_code = SCWindMapping.from_value(fan_speed)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain="roborock",
                translation_key="invalid_fan_speed",
                translation_placeholders={"fan_speed": fan_speed},
            ) from err
        try:
            await async_with_retry(
                "set_fan_speed",
                self.coordinator.api.set_fan_speed,
                fan_speed_code,
            )
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": "set_fan_speed"},
            ) from err
        await self.coordinator.async_refresh()

    RoborockQ7Vacuum.get_maps = q7_get_maps
    RoborockQ7Vacuum.async_get_segments = q7_async_get_segments
    RoborockQ7Vacuum.async_clean_segments = q7_async_clean_segments
    RoborockQ7Vacuum.get_vacuum_current_position = q7_get_vacuum_current_position
    RoborockQ7Vacuum.async_set_fan_speed = q7_async_set_fan_speed
    applied += [
        "Q7.get_maps",
        "Q7.get_segments",
        "Q7.clean_segments",
        "Q7.position",
        "Q7.fan_speed_refresh",
    ]

    # CLEAN_AREA must be advertised for the UI room-clean picker and the
    # service's capability check. Core's feature plumbing varies between
    # releases: ``_attr_supported_features`` may be a plain class
    # attribute or a class-level ``property`` (reading it at patch time
    # crashed with "unsupported operand type(s) for |: 'property' and
    # 'VacuumEntityFeature'"), and ``supported_features`` may be a
    # cached_property. So nothing is read from or written to the class
    # attribute here. Instead ``supported_features`` is replaced with a
    # data descriptor that ORs CLEAN_AREA in at read time: it resolves
    # whatever core keeps in ``_attr_supported_features`` per instance
    # (instance overrides still win for the base flags) and, being a
    # data descriptor, also defeats any stale cached_property value -
    # so setup order does not matter.
    if not getattr(RoborockQ7Vacuum, "_b01_clean_area", False):

        def _supported_features(self):
            """Patched by roborock_b01: adds CLEAN_AREA to the Q7."""
            features = self._attr_supported_features
            if not features & VacuumEntityFeature.CLEAN_AREA:
                return features | VacuumEntityFeature.CLEAN_AREA
            return features

        RoborockQ7Vacuum.supported_features = property(_supported_features)
        RoborockQ7Vacuum._b01_clean_area = True
    applied.append("Q7.CLEAN_AREA")

    # ============================== Q10 ==============================

    async def q10_get_maps(self) -> dict:
        """Current map + rooms from the push-driven Q10 map trait."""
        api = self.coordinator.api
        try:
            await async_with_retry("request_dps", api.map.refresh)
            await async_with_retry("get_map_list", api.maps.refresh)
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
        rooms = {room.id: room.name or f"Room {room.id}" for room in api.map.rooms}
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

    # The Q10's CLEAN_AREA advertisement is core's own working surface
    # and is deliberately not touched (writing the class attribute is
    # the same release-shape landmine the Q7 patch just avoided). The
    # service handler's capability check is the runtime gate.

    # ====================== Q7 wire guard ======================
    # Wrap the concrete Q7 channel's two outgoing methods so no command
    # that can destroy/corrupt the saved map (or an unverified point/
    # zone payload) can ever leave Home Assistant - regardless of which
    # integration, script or UI sends it. The channel instances used by
    # HA are built by the library factory; the factory wrapper marks
    # every channel it returns as guarded.
    from roborock.devices.rpc import b01_q7_channel as _q7_channel_mod
    from roborock.devices.rpc.b01_q7_channel import B01Q7Channel

    if not getattr(B01Q7Channel, "_b01_wire_guard", False):
        B01Q7Channel._b01_wire_guard = True
        _orig_send_command = B01Q7Channel.send_command
        _orig_send_map_command = B01Q7Channel.send_map_command

        async def _guarded_send_command(self, command, params=None):
            assert_q7_command_allowed(command)
            return await _orig_send_command(self, command, params)

        async def _guarded_send_map_command(self, command, params=None):
            assert_q7_command_allowed(command)
            return await _orig_send_map_command(self, command, params)

        B01Q7Channel.send_command = _guarded_send_command
        B01Q7Channel.send_map_command = _guarded_send_map_command

        _orig_create_channel = _q7_channel_mod.create_b01_q7_channel

        def _guarding_create_b01_q7_channel(device, product, mqtt_channel):
            channel = _orig_create_channel(device, product, mqtt_channel)
            channel._b01_command_guard = True
            return channel

        _q7_channel_mod.create_b01_q7_channel = _guarding_create_b01_q7_channel
    applied.append("Q7.wire_guard")

    return applied
