"""Owned vacuum entities for the standalone Roborock B01 integration.

The entity behavior (state tables, command mapping, feature flags) is
copied from Home Assistant core's roborock component (Apache License
2.0, https://github.com/home-assistant/core - homeassistant/components/
roborock/vacuum.py) and extended with this integration's verified
features: Q7 room cleaning with the pre-send room-id guard, Q7
get_maps/get_vacuum_current_position, the immediate coordinator refresh
after writes, and the bounded-retry failsafe layer.

Everything runs over the python-roborock library directly (own session,
own coordinators) - no dependency on the official Roborock integration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from roborock.data.b01_q7.b01_q7_code_mappings import (
    CleanTaskTypeMapping,
    SCDeviceCleanParam,
    SCWindMapping,
    WorkStatusMapping,
)
from roborock.data.b01_q10.b01_q10_code_mappings import (
    B01_Q10_DP,
    YXDeviceState,
    YXFanLevel,
)
from roborock.data.b01_q10.b01_q10_containers import Q10RoborockPoint
from roborock.exceptions import RoborockException

from .const import DOMAIN
from .coordinators import B01Q7Coordinator, B01Q10Coordinator
from .failsafe import async_with_retry
from .vacuum import (
    _ROOMS_POLL_INTERVAL,
    _ROOMS_WAIT_TIMEOUT,
    _q7_current_room_ids,
    _q7_room_names,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# State tables copied from core's roborock vacuum component.
Q7_STATE_CODE_TO_STATE = {
    WorkStatusMapping.SLEEPING: VacuumActivity.IDLE,
    WorkStatusMapping.WAITING_FOR_ORDERS: VacuumActivity.IDLE,
    WorkStatusMapping.PAUSED: VacuumActivity.PAUSED,
    # An untouched pause falls asleep after ~10 minutes; the job is kept and
    # resumes on the next start, so this is a pause rather than an idle state.
    WorkStatusMapping.WORKING_SLEEP: VacuumActivity.PAUSED,
    WorkStatusMapping.DOCKING: VacuumActivity.RETURNING,
    WorkStatusMapping.CHARGING: VacuumActivity.DOCKED,
    WorkStatusMapping.SWEEP_MOPING: VacuumActivity.CLEANING,
    WorkStatusMapping.SWEEP_MOPING_2: VacuumActivity.CLEANING,
    WorkStatusMapping.MOPING: VacuumActivity.CLEANING,
    WorkStatusMapping.UPDATING: VacuumActivity.DOCKED,
    WorkStatusMapping.MOP_CLEANING: VacuumActivity.DOCKED,
    WorkStatusMapping.MOP_AIRDRYING: VacuumActivity.DOCKED,
}

Q10_STATE_CODE_TO_STATE = {
    YXDeviceState.SLEEPING: VacuumActivity.IDLE,
    YXDeviceState.IDLE: VacuumActivity.IDLE,
    YXDeviceState.CLEANING: VacuumActivity.CLEANING,
    YXDeviceState.RETURNING_HOME: VacuumActivity.RETURNING,
    YXDeviceState.REMOTE_CONTROL_ACTIVE: VacuumActivity.CLEANING,
    YXDeviceState.CHARGING: VacuumActivity.DOCKED,
    YXDeviceState.PAUSED: VacuumActivity.PAUSED,
    YXDeviceState.ERROR: VacuumActivity.ERROR,
    YXDeviceState.UPDATING: VacuumActivity.DOCKED,
    YXDeviceState.EMPTYING_THE_BIN: VacuumActivity.DOCKED,
    YXDeviceState.MAPPING: VacuumActivity.CLEANING,
    YXDeviceState.RELOCATING: VacuumActivity.CLEANING,
    YXDeviceState.SWEEPING: VacuumActivity.CLEANING,
    YXDeviceState.MOPPING: VacuumActivity.CLEANING,
    YXDeviceState.SWEEP_AND_MOP: VacuumActivity.CLEANING,
    YXDeviceState.TRANSITIONING: VacuumActivity.CLEANING,
    YXDeviceState.WAITING_TO_CHARGE: VacuumActivity.DOCKED,
}


def _command_failed(command: str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"command": command},
    )


class B01VacuumEntity(StateVacuumEntity):
    """Shared plumbing: coordinator glue, device info, availability."""

    _attr_has_entity_name = True
    _attr_translation_key = DOMAIN
    _attr_name = None

    def __init__(self, coordinator) -> None:
        """Initialize from the per-device coordinator."""
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.duid}"
        self._attr_device_info = {
            "identifiers": {("roborock", coordinator.duid)},
            "name": coordinator.device.name,
            "manufacturer": "Roborock",
            "model": coordinator.device.product.model,
        }

    @property
    def available(self) -> bool:
        """Available when the device connection is up and we have data."""
        return self.coordinator.last_update_success and (
            self.coordinator.device.is_connected
        )


class B01Q7Vacuum(B01VacuumEntity):
    """Q7 (sc*, pv=B01) vacuum: core behavior + map/room features."""

    _attr_supported_features = (
        VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.SEND_COMMAND
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.START
        | VacuumEntityFeature.CLEAN_AREA
    )

    def __init__(self, coordinator: B01Q7Coordinator) -> None:
        super().__init__(coordinator)

    # ---------------------------------------------------------- state

    @property
    def fan_speed_list(self) -> list[str]:
        """Get the list of available fan speeds."""
        return SCWindMapping.keys()

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the status of the vacuum cleaner."""
        data = self.coordinator.data
        if data is not None and data.status is not None:
            return Q7_STATE_CODE_TO_STATE.get(data.status)
        return None

    @property
    def battery_level(self) -> int | None:
        """Battery percent (Q7 reports it as the 'quantity' property)."""
        data = self.coordinator.data
        if data is not None and data.quantity is not None:
            return min(100, max(0, data.quantity))
        return None

    @property
    def fan_speed(self) -> str | None:
        """Return the fan speed of the vacuum cleaner."""
        return self.coordinator.data.wind_name if self.coordinator.data else None

    # -------------------------------------------------------- controls

    async def async_start(self) -> None:
        """Start the vacuum."""
        try:
            await async_with_retry("start_clean", self.coordinator.api.start_clean)
        except RoborockException as err:
            raise _command_failed("start_clean") from err
        await self.coordinator.async_refresh()

    async def async_pause(self) -> None:
        """Pause the vacuum."""
        try:
            await async_with_retry("pause_clean", self.coordinator.api.pause_clean)
        except RoborockException as err:
            raise _command_failed("pause_clean") from err
        await self.coordinator.async_refresh()

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum."""
        try:
            await async_with_retry("stop_clean", self.coordinator.api.stop_clean)
        except RoborockException as err:
            raise _command_failed("stop_clean") from err
        await self.coordinator.async_refresh()

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send vacuum back to base."""
        try:
            await async_with_retry(
                "return_to_dock", self.coordinator.api.return_to_dock
            )
        except RoborockException as err:
            raise _command_failed("return_to_dock") from err
        await self.coordinator.async_refresh()

    async def async_locate(self, **kwargs: Any) -> None:
        """Locate vacuum."""
        try:
            await async_with_retry("find_me", self.coordinator.api.find_me)
        except RoborockException as err:
            raise _command_failed("find_me") from err

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set vacuum fan speed."""
        try:
            fan_speed_code = SCWindMapping.from_value(fan_speed)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_fan_speed",
                translation_placeholders={"fan_speed": fan_speed},
            ) from err
        try:
            await async_with_retry(
                "set_fan_speed", self.coordinator.api.set_fan_speed, fan_speed_code
            )
        except RoborockException as err:
            raise _command_failed("set_fan_speed") from err
        # The vacuum UI reads coordinator.data (wind_name); refresh now
        # instead of waiting for the next poll.
        await self.coordinator.async_refresh()

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a command to a vacuum cleaner (through the wire guard)."""
        try:
            await async_with_retry(
                "send_command", self.coordinator.api.send, command, params
            )
        except RoborockException as err:
            raise _command_failed(command) from err
        await self.coordinator.async_refresh()

    # --------------------------------------- room / map (integration's own)

    async def get_maps(self) -> dict:
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
        return {
            "maps": [
                {
                    "flag": map_id,
                    "name": f"Map {map_id}" if map_id is not None else "Map",
                    "rooms": rooms,
                }
            ]
        }

    async def async_get_segments(self) -> list[Segment]:
        """Rooms as segments for the room-cleaning UI."""
        response = await self.get_maps()
        first = response["maps"][0] if response["maps"] else None
        if first is None:
            return []
        return [
            Segment(id=str(room_id), name=name, group=first["name"])
            for room_id, name in first["rooms"].items()
        ]

    async def async_clean_segments(self, segment_ids: list[str], **kwargs) -> None:
        """Clean rooms by id via the library's SET_ROOM_CLEAN wrapper.

        Failsafe: empty or malformed ids are refused before anything
        goes on the wire, and ids are verified against the robot's
        current room list so unknown ids can never trigger the device's
        full-house fallback.
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
        _LOGGER.debug(
            "roborock_b01: %s clean_segments -> service.set_room_clean "
            "{clean_type: %d, ctrl_value: %d, room_ids: %s}",
            self.entity_id,
            CleanTaskTypeMapping.ROOM.code,
            SCDeviceCleanParam.START.code,
            ids,
        )
        try:
            await async_with_retry(
                "clean_segments", self.coordinator.api.clean_segments, ids
            )
        except RoborockException as err:
            raise _command_failed("clean_segments") from err
        await self.coordinator.async_refresh()

    async def get_vacuum_current_position(self) -> dict:
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

    # Q7 point/zone cleaning: no verified wire payload exists (the wire
    # guard blocks the unverified shapes); the standard "not supported"
    # error from StateVacuumEntity applies.


class B01Q10Vacuum(B01VacuumEntity):
    """Q10 (ss*, pv=B01) vacuum: core behavior copied 1:1."""

    _attr_supported_features = (
        VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.SEND_COMMAND
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.START
        | VacuumEntityFeature.CLEAN_AREA
    )
    _attr_fan_speed_list = tuple(
        fan_level.value for fan_level in YXFanLevel if fan_level != YXFanLevel.UNKNOWN
    )

    def __init__(self, coordinator: B01Q10Coordinator) -> None:
        super().__init__(coordinator)

    async def async_added_to_hass(self) -> None:
        """Register trait listener for push-based status updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.api.status.add_update_listener(self.async_write_ha_state)
        )

    # ---------------------------------------------------------- state

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the status of the vacuum cleaner."""
        if self.coordinator.api.status.status is not None:
            return Q10_STATE_CODE_TO_STATE.get(self.coordinator.api.status.status)
        return None

    @property
    def battery_level(self) -> int | None:
        """Battery percent from the push-driven status trait."""
        return self.coordinator.api.status.battery

    @property
    def fan_speed(self) -> str | None:
        """Return the fan speed of the vacuum cleaner."""
        if (fan_level := self.coordinator.api.status.fan_level) is not None:
            return fan_level.value
        return None

    # -------------------------------------------------------- controls

    async def async_start(self) -> None:
        """Start the vacuum."""
        try:
            await self.coordinator.api.vacuum.start_clean()
        except RoborockException as err:
            raise _command_failed("start_clean") from err

    async def async_pause(self) -> None:
        """Pause the vacuum."""
        try:
            await self.coordinator.api.vacuum.pause_clean()
        except RoborockException as err:
            raise _command_failed("pause_clean") from err

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum."""
        try:
            await self.coordinator.api.vacuum.stop_clean()
        except RoborockException as err:
            raise _command_failed("stop_clean") from err

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send vacuum back to base."""
        try:
            await self.coordinator.api.vacuum.return_to_dock()
        except RoborockException as err:
            raise _command_failed("return_to_dock") from err

    async def async_locate(self, **kwargs: Any) -> None:
        """Locate vacuum."""
        try:
            await self.coordinator.api.command.send(B01_Q10_DP.SEEK)
        except RoborockException as err:
            raise _command_failed("find_me") from err

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set vacuum fan speed."""
        try:
            fan_level = YXFanLevel.from_value(fan_speed)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_fan_speed",
                translation_placeholders={"fan_speed": fan_speed},
            ) from err
        try:
            await self.coordinator.api.vacuum.set_fan_level(fan_level)
        except RoborockException as err:
            raise _command_failed("set_fan_speed") from err

    async def async_get_segments(self) -> list[Segment]:
        """Get the segments that can be cleaned."""
        return [
            Segment(id=str(room.id), name=room.name)
            for room in self.coordinator.api.map.rooms
        ]

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Clean the specified segments."""
        try:
            await self.coordinator.api.vacuum.clean_segments(
                [int(seg_id) for seg_id in segment_ids]
            )
        except RoborockException as err:
            raise _command_failed("clean_segments") from err

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a command to a vacuum cleaner.

        The command string can be an enum name (e.g. "SEEK"), a DP
        string value (e.g. "dpSeek"), or an integer code.
        """
        if (dp_command := B01_Q10_DP.from_any_optional(command)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_command",
                translation_placeholders={"command": command},
            )
        try:
            await self.coordinator.api.command.send(dp_command, params=params)
        except RoborockException as err:
            raise _command_failed(command) from err

    # ------------------------------------------------- roborock services

    async def get_maps(self) -> dict:
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
        # Room names ride in with the next map push (the map trait is
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

    async def get_vacuum_current_position(self) -> dict:
        """Get the current position of the vacuum from the map."""
        if (position := self.coordinator.api.map.robot_position) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="position_not_found",
            )
        return {"x": position.x, "y": position.y}

    async def async_set_vacuum_goto_position(self, x: int, y: int) -> None:
        """Move the Q10 to a position using the library goto operation."""
        try:
            await self.coordinator.api.vacuum.goto_position(Q10RoborockPoint(x, y))
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_q10_coordinate",
                translation_placeholders={"x": str(x), "y": str(y)},
            ) from err
        except RoborockException as err:
            raise _command_failed("set_vacuum_goto_position") from err

    async def async_set_vacuum_zoned_cleaning(
        self, x1: int, y1: int, x2: int, y2: int, repeats: int
    ) -> None:
        """Clean the specified zone."""
        try:
            # Home Assistant defines repeats as additional passes, while Q10
            # carries the total clean count.
            await self.coordinator.api.vacuum.clean_zone(
                Q10RoborockPoint(x1, y1),
                Q10RoborockPoint(x2, y2),
                clean_count=repeats + 1,
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_q10_zone",
            ) from err
        except RoborockException as err:
            raise _command_failed("set_vacuum_zoned_cleaning") from err


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Create the owned vacuum entities for this entry's devices."""
    from .hub import get_coordinators

    entities = []
    for coordinator in get_coordinators(hass, entry):
        if isinstance(coordinator, B01Q7Coordinator):
            entities.append(B01Q7Vacuum(coordinator))
        elif isinstance(coordinator, B01Q10Coordinator):
            entities.append(B01Q10Vacuum(coordinator))
    if entities:
        async_add_entities(entities)
