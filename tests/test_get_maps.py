"""Tests for get_maps / get_vacuum_current_position on both models.

Q7: full wire path (GET_MAP_LIST + UPLOAD_BY_MAPID) parsed by the real
B01MapParser from a synthetic SCMap protobuf. Q10: push-driven trait fed a
real Q10MapPacket plus the MULTI_MAP DPS response.
"""

from __future__ import annotations

import pytest
from conftest import make_scmap
from homeassistant.components.roborock.vacuum import (
    RoborockQ7Vacuum,
    RoborockQ10Vacuum,
)
from homeassistant.exceptions import HomeAssistantError
from roborock.data.b01_q10.b01_q10_code_mappings import B01_Q10_DP
from roborock.map.b01_q10_map_parser import Q10MapPacket, Q10Room
from roborock.roborock_typing import RoborockB01Q7Methods

# --------------------------------------------------------------------------
# Q7
# --------------------------------------------------------------------------


async def test_q7_get_maps_full_wire_path(q7, q7_entity):
    """get_maps drives both map commands and returns rooms + map id."""
    q7["rpc"].responses = [{"map_list": [{"id": 7, "cur": 1}]}]
    q7["map"].push_payload = make_scmap(
        rooms=((16, "Kitchen"), (17, "Bedroom"))
    )

    result = await q7_entity.get_maps()

    # Wire: GET_MAP_LIST on the rpc channel, UPLOAD_BY_MAPID on the map one.
    assert q7["rpc"].published[0][0] == RoborockB01Q7Methods.GET_MAP_LIST
    assert q7["map"].map_commands == [
        (RoborockB01Q7Methods.UPLOAD_BY_MAPID, {"map_id": 7})
    ]
    assert result["maps"][0]["flag"] == 7
    assert result["maps"][0]["rooms"] == {16: "Kitchen", 17: "Bedroom"}


async def test_q7_get_vacuum_current_position(q7, q7_entity):
    """Position comes from the parsed live map (map pixel coordinates)."""
    q7["rpc"].responses = [{"map_list": [{"id": 7, "cur": 1}]}]
    q7["map"].push_payload = make_scmap()

    pos = await q7_entity.get_vacuum_current_position()
    assert pos == {"x": 2, "y": 7}


async def test_q7_device_error_mapped(q7, q7_entity):
    """A RoborockException from the device becomes a translated HA error."""
    q7["rpc"].fail_next = True
    with pytest.raises(HomeAssistantError) as exc_info:
        await q7_entity.get_maps()
    assert exc_info.value.translation_key == "map_failure"


async def test_q7_async_get_segments(q7, q7_entity):
    """Segments mirror the room dict, grouped under the map name."""
    q7["rpc"].responses = [{"map_list": [{"id": 7, "cur": 1}]}]
    q7["map"].push_payload = make_scmap(rooms=((16, "Kitchen"),))

    segments = await q7_entity.async_get_segments()
    assert [(s.id, s.name, s.group) for s in segments] == [
        ("16", "Kitchen", "Map 7")
    ]


def test_q7_get_maps_absent_in_core(q7_entity):
    """The patch supplies methods core's Q7 stub lacks (regression guard)."""
    assert "get_maps" in RoborockQ7Vacuum.__dict__


# --------------------------------------------------------------------------
# Q10
# --------------------------------------------------------------------------


def _feed_q10_maps(api):
    """Feed a real map packet (rooms + image) and the map-list DPS."""
    api.map.update_from_map_packet(
        Q10MapPacket(
            map_id=3,
            width=8,
            height=8,
            grid=bytes([2] * 64),
            rooms=[
                Q10Room(id=5, raw_name="Living", pixel_value=2, pixel_count=64),
                Q10Room(id=6, raw_name="Office", pixel_value=3, pixel_count=0),
            ],
        )
    )
    api.maps.update_from_dps(
        {
            B01_Q10_DP.MULTI_MAP: {
                "op": "list",
                "result": 1,
                "data": [{"id": "3", "name": "Main", "timestamp": 123}],
            }
        }
    )


async def test_q10_get_maps_push_driven(q10, q10_entity):
    """get_maps kicks the read-only refreshes and reads the pushed state."""
    _feed_q10_maps(q10["api"])

    result = await q10_entity.get_maps()

    # Refreshes only kick the stream: one dpRequestDps read + the list op.
    assert q10["channel"].published[0][0] == B01_Q10_DP.REQUEST_DPS
    assert q10["channel"].published[1] == (B01_Q10_DP.COMMON, {"61": {"op": "list"}})
    assert result["maps"][0]["flag"] == "3"
    assert result["maps"][0]["rooms"] == {5: "Living", 6: "Office"}


async def test_q10_get_maps_waits_for_push(q10, q10_entity):
    """Rooms arrive after a short push delay; get_maps waits for them."""
    api = q10["api"]

    async def push_later():
        import asyncio

        await asyncio.sleep(0.05)
        _feed_q10_maps(api)

    import asyncio

    task = asyncio.get_running_loop().create_task(push_later())
    result = await q10_entity.get_maps()
    await task
    assert result["maps"][0]["rooms"] == {5: "Living", 6: "Office"}


async def test_q10_device_error_mapped(q10, q10_entity):
    """A failing kick becomes a translated HA error."""
    q10["channel"].fail_next = True
    with pytest.raises(HomeAssistantError) as exc_info:
        await q10_entity.get_maps()
    assert exc_info.value.translation_key == "map_failure"


def test_q10_get_maps_absent_in_core(q10_entity):
    """The patch supplies get_maps core's Q10 stub lacks (regression guard)."""
    assert "get_maps" in RoborockQ10Vacuum.__dict__
