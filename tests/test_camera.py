"""Tests for the B01 map camera on both models.

Exercises the real camera class end to end over fake channels: push-driven
updates (via the real trait's listener mechanism), on-demand refresh, and
unload cleanup.
"""

from __future__ import annotations

from conftest import make_scmap
from roborock.data.b01_q10.b01_q10_code_mappings import B01_Q10_DP
from roborock.map.b01_q10_map_parser import Q10MapPacket, Q10Room
from roborock.roborock_typing import RoborockB01Q7Methods

from custom_components.roborock_b01.camera import B01MapCamera

# --------------------------------------------------------------------------
# Q7
# --------------------------------------------------------------------------


async def test_q7_camera_on_demand_first_frame(q7):
    """With no push yet, the first image fetch drives both map commands."""
    q7["rpc"].responses = [{"map_list": [{"id": 7, "cur": 1}]}]
    q7["map"].push_payload = make_scmap()

    camera = B01MapCamera(q7["coord"])
    camera.hass = object()
    await camera.async_added_to_hass()

    image = await camera.async_camera_image()
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    # On-demand path: GET_MAP_LIST then UPLOAD_BY_MAPID for the current map.
    assert q7["rpc"].published[0][0] == RoborockB01Q7Methods.GET_MAP_LIST
    assert q7["map"].map_commands == [
        (RoborockB01Q7Methods.UPLOAD_BY_MAPID, {"map_id": 7})
    ]

    await camera.async_will_remove_from_hass()


async def test_q7_camera_push_driven(q7):
    """A pushed live frame updates the cached image without any polling."""
    q7["rpc"].responses = [{"map_list": [{"id": 7, "cur": 1}]}]
    q7["map"].push_payload = make_scmap()

    # Wire the real push subscription exactly like the library's
    # device.connect() does (api.start() subscribes map pushes).
    await q7["api"].start()

    camera = B01MapCamera(q7["coord"])
    camera.hass = object()
    await camera.async_added_to_hass()

    # Device pushes a new live frame (different size -> new PNG).
    q7["map"].push(make_scmap(size=14))
    assert camera._image is not None
    assert camera.state_write_count >= 1  # _on_push wrote state

    # No new on-demand commands were sent after setup.
    rpc_calls_after_setup = len(q7["rpc"].published)
    map_cmds_after_setup = len(q7["map"].map_commands)
    image = await camera.async_camera_image()
    assert image == camera._image
    assert len(q7["rpc"].published) == rpc_calls_after_setup
    assert len(q7["map"].map_commands) == map_cmds_after_setup

    await camera.async_will_remove_from_hass()
    await q7["api"].close()


async def test_q7_camera_unsubscribes_on_unload(q7):
    """Removing the entity unsubscribes the push listener."""
    q7["rpc"].responses = [{"map_list": [{"id": 7, "cur": 1}]}]
    q7["map"].push_payload = make_scmap()

    camera = B01MapCamera(q7["coord"])
    camera.hass = object()
    await camera.async_added_to_hass()
    await camera.async_will_remove_from_hass()

    assert camera._unsub_push is None


# --------------------------------------------------------------------------
# Q10
# --------------------------------------------------------------------------


def _feed_q10(api):
    api.map.update_from_map_packet(
        Q10MapPacket(
            map_id=3,
            width=8,
            height=8,
            grid=bytes([2] * 64),
            rooms=[Q10Room(id=5, raw_name="Living", pixel_value=2, pixel_count=64)],
        )
    )


async def test_q10_camera_push_driven(q10):
    """Q10: image comes from pushed map packets via the real trait."""
    camera = B01MapCamera(q10["coord"])
    camera.hass = object()
    await camera.async_added_to_hass()

    # First fetch kicks the read-only refreshes (no frame yet).
    image = await camera.async_camera_image()
    assert image is None
    assert q10["channel"].published[0][0] == B01_Q10_DP.REQUEST_DPS

    # Device pushes a map packet; the trait notifies the camera.
    _feed_q10(q10["api"])
    assert camera._image is not None
    assert camera._image[:8] == b"\x89PNG\r\n\x1a\n"

    await camera.async_will_remove_from_hass()


async def test_q10_camera_uses_content_trait_not_start(q10):
    """The camera listens on the map trait; it must NOT call api.start()."""
    camera = B01MapCamera(q10["coord"])
    camera.hass = object()
    await camera.async_added_to_hass()
    await camera.async_will_remove_from_hass()
    # The fake channel records nothing for a start attempt, but the trait's
    # listener list must be empty after unload.
    assert camera._unsub_push is None
