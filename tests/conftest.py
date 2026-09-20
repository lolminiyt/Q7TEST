"""Shared fixtures: real python-roborock traits + minimal HA fakes.

The python-roborock library is NOT mocked - real Q7PropertiesApi /
Q10PropertiesApi are constructed over fake channels that capture every
published command and serve scripted responses, so assertions verify the
exact wire payloads.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs

ha_stubs.install()

from homeassistant.components.roborock.vacuum import (
    RoborockQ7Vacuum as _StubQ7,
    RoborockQ10Vacuum as _StubQ10,
)
from roborock.data.containers import HomeDataDevice, HomeDataProduct
from roborock.devices.rpc.b01_q7_channel import (
    Q7MapRpcChannel,
    Q7RpcChannel,
)
from roborock.devices.rpc.b01_q10_channel import B01Q10Channel
from roborock.devices.traits.b01.q7 import Q7PropertiesApi
from roborock.devices.traits.b01.q10 import Q10PropertiesApi

from custom_components.roborock_b01.vacuum import (
    patch_b01_vacuum_classes,
)

DEVICE = HomeDataDevice.from_dict(
    {"duid": "duid123", "sn": "SN123", "name": "Q7", "local_key": "k", "product_id": "pid"}
)
PRODUCT = HomeDataProduct.from_dict(
    {"id": "pid", "model": "roborock.q7m5", "name": "Q7 M5", "category": "robot.vacuum.cleaner"}
)


class FakeQ7Channel(Q7RpcChannel):
    """Captures commands, serves queued responses."""

    def __init__(self):
        self.published = []  # (command, params)
        self.responses: list[Any] = []
        self.fail_next = False

    async def send_command(self, command, params=None):
        if self.fail_next:
            self.fail_next = False
            from roborock.exceptions import RoborockException

            raise RoborockException("device offline")
        self.published.append((command, params))
        if self.responses:
            return self.responses.pop(0)
        return {"result": "ok"}


class FakeQ7MapChannel(Q7MapRpcChannel):
    def __init__(self):
        self.map_commands = []
        self.push_listeners = []
        self.push_payload: bytes | None = None

    async def send_map_command(self, command, params=None):
        self.map_commands.append((command, params))
        return self.push_payload or b""

    async def subscribe_map_pushes(self, callback):
        self.push_listeners.append(callback)
        return lambda: None

    def push(self, payload: bytes):
        self.push_payload = payload
        for cb in self.push_listeners:
            cb(payload)


class FakeQ10Channel(B01Q10Channel):
    def __init__(self):
        self.published = []
        self.responses: list[Any] = []
        self.fail_next = False
        self._subscribers = []

    async def send_command(self, command, params=None):
        if self.fail_next:
            self.fail_next = False
            from roborock.exceptions import RoborockException

            raise RoborockException("device offline")
        self.published.append((command, params))
        if self.responses:
            return self.responses.pop(0)
        return {"result": "ok"}

    def subscribe(self, callback):
        self._subscribers.append(callback)
        return lambda: None

    def is_connected(self):
        return True


def make_scmap(rooms=((16, "Room16"), (17, "Room17")), size=10):
    """Build a real SCMap protobuf the library parser accepts."""
    from roborock.map.proto.b01_scmap_pb2 import RobotMap

    res = 0.05
    rm = RobotMap()
    rm.mapType = 0
    rm.mapHead.sizeX = size
    rm.mapHead.sizeY = size
    rm.mapHead.minX = 0
    rm.mapHead.minY = 0
    rm.mapHead.maxX = size * res
    rm.mapHead.maxY = size * res
    rm.mapHead.resolution = res
    grid = bytearray(size * size)
    for y in range(size):
        for x in range(size):
            if x in (0, size - 1) or y in (0, size - 1) or x == 5:
                grid[y * size + x] = 128
            else:
                grid[y * size + x] = 127
    rm.mapData.mapData = bytes(grid)
    for i, (rid, name) in enumerate(rooms):
        col = 0.125 + i * 0.2
        info = rm.roomDataInfo.add()
        info.roomId = rid
        info.roomName = name
        info.roomNamePost.x = col
        info.roomNamePost.y = 0.125
        ol = rm.roomOutline.add()
        ol.roomId = rid
        for px, py in ((1, 1), (4, 1), (4, 8), (1, 8)):
            pt = ol.points.add()
            pt.x = px
            pt.y = py
    rm.currentPose.x = 0.1
    rm.currentPose.y = 0.1
    rm.currentPose.phi = 0.0
    rm.chargeStation.x = 0.05
    rm.chargeStation.y = 0.05
    rm.chargeStation.phi = 0.0
    return rm.SerializeToString()


class FakeQ7Coordinator:
    def __init__(self, api):
        self.api = api
        self.duid = DEVICE.duid


class FakeQ10Coordinator:
    def __init__(self, api):
        self.api = api
        self.duid = DEVICE.duid


def make_q7():
    """Return (api, coordinator, rpc, map_rpc) with real Q7 traits."""
    rpc, map_rpc = FakeQ7Channel(), FakeQ7MapChannel()
    api = Q7PropertiesApi(rpc, map_rpc, DEVICE, PRODUCT)
    return api, FakeQ7Coordinator(api), rpc, map_rpc


def make_q10():
    """Return (api, coordinator, channel) with real Q10 traits."""
    channel = FakeQ10Channel()
    api = Q10PropertiesApi(channel)
    return api, FakeQ10Coordinator(api), channel


@pytest.fixture
def q7():
    api, coord, rpc, map_rpc = make_q7()
    yield {"api": api, "coord": coord, "rpc": rpc, "map": map_rpc}


@pytest.fixture
def q10():
    api, coord, channel = make_q10()
    yield {"api": api, "coord": coord, "channel": channel}


@pytest.fixture(scope="session", autouse=True)
def _patched():
    """Apply the B01 patches once for the whole session."""
    return patch_b01_vacuum_classes()


@pytest.fixture
def q7_entity(q7, _patched):
    return _StubQ7(q7["coord"])


@pytest.fixture
def q10_entity(q10, _patched):
    return _StubQ10(q10["coord"])
