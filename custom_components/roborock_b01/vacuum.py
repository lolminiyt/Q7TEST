"""B01 vacuumentity patches for HA core RoborockQ7Vacuum / RoborockQ10Vacuum.

B01 background (python-roborock docs/DEVICES.md):
- Q7 (Q7 BF/TF/M5/L5, pv=B01): Q7PropertiesApi on
  coordinator.api with map (GET_MAP_LIST), map_content
  (UPLOAD_BY_MAPID -> MapData.rooms / vacuum_position / image_content),
  clean_segments / start_clean / pause / stop / dock / find_me /
  fan / water / mode / child lock / dust collection / DND.
- Q10 (pv=B01): Q10PropertiesApi with vacuum.*, command.send(),
  map.rooms / robot_position, maps.current_map_id.

Core HA leaves Q7 get_maps/position/goto/zone as ServiceNotSupported and
gives Q7 no segments; Q10 get_maps is also ServiceNotSupported. Everything
here reuses the authenticated core session - no extra login.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


def patch_b01_vacuum_classes() -> list[str]:
    """Patch core B01 vacuum classes. Returns list of applied patch names."""
    from homeassistant.components.roborock.vacuum import (
        RoborockQ7Vacuum,
        RoborockQ10Vacuum,
    )
    from homeassistant.components.vacuum import Segment, VacuumEntityFeature
    from homeassistant.exceptions import HomeAssistantError
    from roborock.exceptions import RoborockException

    applied: list[str] = []

    # ---------------- Q7 ----------------

    async def q7_get_maps(self):
        """Current map + rooms in V1 get_maps shape."""
        api = self.coordinator.api
        try:
            await api.map.refresh()
        except RoborockException as err:
            _LOGGER.debug("roborock_b01: Q7 map.refresh failed: %s", err)
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="map_failure",
            ) from err
        try:
            await api.map_content.refresh()
        except RoborockException as err:
            _LOGGER.debug("roborock_b01: Q7 map_content.refresh failed: %s", err)
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="map_failure",
            ) from err

        map_id = api.map.current_map_id
        map_data = api.map_content.map_data
        rooms: dict[int, str] = {}
        map_name = f"Map {map_id}" if map_id is not None else "Map 0"
        if map_data is not None:
            if map_data.map_name:
                map_name = map_data.map_name
            if map_data.rooms:
                for rid, room in map_data.rooms.items():
                    rooms[int(rid)] = room.name or f"Room {rid}"
            if not rooms:
                raw_names = (map_data.additional_parameters or {}).get(
                    "room_names"
                ) or {}
                for rid, name in raw_names.items():
                    try:
                        rooms[int(rid)] = str(name)
                    except (TypeError, ValueError):
                        continue
        return {
            "maps": [
                {
                    "flag": map_id,
                    "name": map_name,
                    "rooms": rooms,  # type: ignore[dict-item]
                }
            ]
        }

    async def q7_async_get_segments(self) -> list[Segment]:
        """Rooms as vacuum segments for the room-cleaning UI."""
        res = await q7_get_maps(self)
        maps = res.get("maps") or []
        if not maps:
            return []
        first = maps[0]
        group = first.get("name")
        rooms = first.get("rooms") or {}
        return [
            Segment(id=str(rid), name=str(name), group=group)
            for rid, name in rooms.items()
        ]

    async def q7_async_clean_segments(self, segment_ids: list[str], **kwargs) -> None:
        """Clean rooms by id via SET_ROOM_CLEAN."""
        try:
            ids = [int(s) for s in segment_ids]
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="command_failed",
                translation_placeholders={"command": "clean_segments"},
            ) from err
        try:
            await self.coordinator.api.clean_segments(ids)
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="command_failed",
                translation_placeholders={"command": "clean_segments"},
            ) from err

    async def q7_get_vacuum_current_position(self):
        """Robot x/y from parsed map content."""
        api = self.coordinator.api
        try:
            await api.map.refresh()
            await api.map_content.refresh()
        except RoborockException as err:
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="map_failure",
            ) from err
        map_data = api.map_content.map_data
        pos = map_data.vacuum_position if map_data else None
        if pos is None:
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="position_not_found",
            )
        return {"x": int(pos.x), "y": int(pos.y)}

    async def q7_goto(self, x: int, y: int) -> None:
        """EXPERIMENTAL goto via service.set_point_clean.

        python-roborock documents no Q7 goto wrapper, so this sends the most
        plausible shape. The device validates and rejects bad params with an
        error (safe) instead of moving. Test supervised. Raw fallback:
        vacuum.send_command with command 'service.set_point_clean'.
        """
        _LOGGER.warning(
            "roborock_b01: EXPERIMENTAL Q7 goto x=%s y=%s", x, y
        )
        try:
            from roborock.roborock_typing import RoborockB01Q7Methods

            await self.coordinator.api.send(
                RoborockB01Q7Methods.SET_POINT_CLEAN, {"x": x, "y": y}
            )
        except RoborockException as err:
            _LOGGER.debug("roborock_b01: Q7 goto failed: %s", err)
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="command_failed",
                translation_placeholders={"command": "set_point_clean"},
            ) from err

    async def q7_zoned_clean(
        self, x1: int, y1: int, x2: int, y2: int, repeats: int
    ) -> None:
        """EXPERIMENTAL zoned clean via service.set_zone_clean.

        Same caveat as goto: best-effort param shape
        ({"zones": [[x1, y1, x2, y2, repeats]]}). Raw fallback:
        vacuum.send_command with command 'service.set_zone_clean'.
        """
        params = {"zones": [[x1, y1, x2, y2, repeats]]}
        _LOGGER.warning("roborock_b01: EXPERIMENTAL Q7 zone clean %s", params)
        try:
            from roborock.roborock_typing import RoborockB01Q7Methods

            await self.coordinator.api.send(
                RoborockB01Q7Methods.SET_ZONE_CLEAN, params
            )
        except RoborockException as err:
            _LOGGER.debug("roborock_b01: Q7 zoned clean failed: %s", err)
            raise HomeAssistantError(
                translation_domain="roborock",
                translation_key="command_failed",
                translation_placeholders={"command": "set_zone_clean"},
            ) from err

    RoborockQ7Vacuum.get_maps = q7_get_maps  # type: ignore[method-assign]
    applied.append("Q7.get_maps")
    RoborockQ7Vacuum.async_get_segments = q7_async_get_segments  # type: ignore[method-assign]
    applied.append("Q7.async_get_segments")
    RoborockQ7Vacuum.async_clean_segments = q7_async_clean_segments  # type: ignore[method-assign]
    applied.append("Q7.async_clean_segments")
    RoborockQ7Vacuum.get_vacuum_current_position = q7_get_vacuum_current_position  # type: ignore[method-assign]
    applied.append("Q7.get_vacuum_current_position")
    RoborockQ7Vacuum.async_set_vacuum_goto_position = q7_goto  # type: ignore[method-assign]
    applied.append("Q7.goto(EXPERIMENTAL)")
    RoborockQ7Vacuum.async_set_vacuum_zoned_cleaning = q7_zoned_clean  # type: ignore[method-assign]
    applied.append("Q7.zoned_clean(EXPERIMENTAL)")

    try:
        RoborockQ7Vacuum._attr_supported_features = (
            RoborockQ7Vacuum._attr_supported_features
            | VacuumEntityFeature.CLEAN_AREA
        )
        applied.append("Q7.CLEAN_AREA flag")
    except Exception as err:  # pragma: no cover
        _LOGGER.debug("roborock_b01: could not add Q7 CLEAN_AREA flag: %s", err)

    # ---------------- Q10 (get_maps only; rest exists in core) ----------------

    async def q10_get_maps(self):
        """Current saved map + rooms from the Q10 map trait."""
        api = self.coordinator.api
        try:
            await api.maps.refresh()
        except RoborockException as err:
            _LOGGER.debug("roborock_b01: Q10 maps.refresh failed: %s", err)
        rooms: dict[int, str] = {}
        for room in api.map.rooms or []:
            try:
                rooms[int(room.id)] = room.name or f"Room {room.id}"
            except (TypeError, ValueError):
                continue
        flag = api.maps.current_map_id
        return {
            "maps": [
                {
                    "flag": flag,
                    "name": f"Map {flag}" if flag is not None else "Map 0",
                    "rooms": rooms,  # type: ignore[dict-item]
                }
            ]
        }

    RoborockQ10Vacuum.get_maps = q10_get_maps  # type: ignore[method-assign]
    applied.append("Q10.get_maps")

    return applied
