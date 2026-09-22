"""Q7 wire guard + shared map/room helpers for the B01 integration.

Two responsibilities, both library-only (no HA core imports - the
integration is standalone):

1. **The wire guard** - the B01Q7Channel class (library-owned, the same
   class every DeviceManager-created Q7 channel instantiates) refuses
   to send any command that could destroy or corrupt the saved map
   (delete/replace/switch maps, room structure changes, schedule
   deletion, and the unverified Q7 point/zone payload shapes).
   Blocking at the channel class means nothing sent through this
   integration - any service, script or UI - can wipe the map.
   The Roborock app is unaffected (it does not go through HA).

2. **Q7 map/room helpers** shared by the vacuum entity, the camera and
   the area-mapping keeper (all verified against python-roborock):

   - ``map`` (MapTrait): ``current_map_id`` after ``refresh()``
     (``service.get_map_list``)
   - ``map_content`` (MapContentTrait): ``refresh()``, ``image_content``
     (PNG bytes), ``map_data`` (vacuum_map_parser_base MapData:
     ``vacuum_position`` {x, y, a}, ``rooms`` {id: Room},
     ``additional_parameters['room_names']`` {id: str}) and
     ``add_update_listener(cb)`` for unsolicited live frames.
   - ``clean_segments(room_ids)`` -> ``service.set_room_clean``
     {clean_type: 1, ctrl_value: 1, room_ids: [...]}  (room clean)

Deliberately NOT implemented: Q7 point/zone cleaning. The library has
no wrapper for it and no verified wire payload shape exists; per this
project's no-guesses rule the shapes are blocked at the wire guard and
the vacuum entity raises core's standard "not supported" error.
"""

from __future__ import annotations

import logging

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


def install_q7_wire_guard() -> str:
    """Install the map-protection guard on the library's Q7 channel.

    Idempotent. Works no matter how the channel is created - the class
    methods are wrapped, so DeviceManager-created channels (our session
    layer) are guarded the same as any other instantiation.
    """
    from roborock.devices.rpc.b01_q7_channel import B01Q7Channel

    if getattr(B01Q7Channel, "_b01_wire_guard", False):
        return "Q7.wire_guard"

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
    return "Q7.wire_guard"
