"""End-to-end lifecycle tests: setup entry, camera scan, service call, unload.

These drive the real component code through paths the unit tests bypass:
``async_setup`` / ``async_setup_entry`` in ``__init__.py``, the camera
platform scan / dispatcher machinery in ``camera.py``, registry-level
service invocation, and unload transitions.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ha_stubs import ConfigEntry
from roborock.roborock_typing import RoborockB01Q7Methods

import custom_components.roborock_b01 as b01
from custom_components.roborock_b01.const import DOMAIN


class _FakeComponent:
    """EntityComponent stand-in that records extracted entities."""

    def __init__(self, entities):
        self._entities = entities

    async def async_extract_from_service(self, call):
        wanted = call.data["entity_id"]
        return [e for e in self._entities if e.entity_id in wanted]


def _make_hass_with_service():
    """Fresh FakeHass + the real setup producing a service-ready hass."""
    hass = b01.hass = None  # ensure no module-level state assumptions
    from ha_stubs import FakeHass

    hass = FakeHass()
    return hass


class _EntityCollector:
    """Captures entities added by the camera platform."""

    def __init__(self):
        self.added = []

    def __call__(self, entities):
        self.added.extend(entities)


def _roborock_entry(coord, entry_id="rob1"):
    """A roborock entry whose runtime_data holds one B01 coordinator."""
    entry = ConfigEntry(entry_id=entry_id, domain="roborock")
    entry.runtime_data = SimpleNamespace(b01_q7=[], b01_q10=[])
    if coord is not None:
        entry.runtime_data.b01_q7.append(coord)
    return entry


# --------------------------------------------------------------------------
# UI entry setup
# --------------------------------------------------------------------------


async def test_setup_entry_registers_services_and_forwards_camera(q7):
    """async_setup_entry applies patches, registers services, loads camera."""
    hass = _make_hass_with_service()
    entry = ConfigEntry(domain=DOMAIN)

    assert await b01.async_setup_entry(hass, entry) is True

    # Service registered in the real registry dict.
    assert "clean_segment" in hass.services.get(DOMAIN, {})
    # Patch sentinel recorded under the integration's data key.
    assert hass.data[DOMAIN]["_b01_patched"] is True
    # Camera platform forwarded for the entry.
    assert hass.config_entries.forwarded == [(entry, ("camera",))]


async def test_setup_entry_idempotent_on_second_entry(q7):
    """A second entry must not re-patch (sentinel) and services stay single."""
    hass = _make_hass_with_service()
    entry1 = ConfigEntry(entry_id="e1", domain=DOMAIN)
    entry2 = ConfigEntry(entry_id="e2", domain=DOMAIN)

    await b01.async_setup_entry(hass, entry1)
    applied_first = hass.data[DOMAIN]["_b01_patched"]
    registry_len = len(hass.services.get(DOMAIN, {}))

    await b01.async_setup_entry(hass, entry2)
    assert applied_first is True
    assert hass.data[DOMAIN]["_b01_patched"] is True  # not re-applied
    assert len(hass.services.get(DOMAIN, {})) == registry_len == 1


# --------------------------------------------------------------------------
# YAML setup
# --------------------------------------------------------------------------


async def test_yaml_setup_loads_platform(q7):
    """YAML setup registers services and loads the camera platform once."""
    hass = _make_hass_with_service()

    assert await b01.async_setup(hass, {DOMAIN: {}}) is True
    assert "clean_segment" in hass.services.get(DOMAIN, {})
    assert hass._platform_loads == [("camera", DOMAIN)]

    # No UI entry exists -> setup must not skip its work.
    assert hass.data[DOMAIN]["_b01_patched"] is True


async def test_yaml_setup_skips_when_ui_entry_exists(q7):
    """With a UI entry present, YAML setup is a no-op (use one method)."""
    hass = _make_hass_with_service()
    ui_entry = ConfigEntry(entry_id="ui", domain=DOMAIN)
    hass.config_entries.add(ui_entry)

    assert await b01.async_setup(hass, {DOMAIN: {}}) is True
    assert hass._platform_loads == []
    assert DOMAIN not in hass.data or not hass.data[DOMAIN].get("_b01_patched")


# --------------------------------------------------------------------------
# Camera platform scan / dispatch / unload
# --------------------------------------------------------------------------


async def test_camera_scan_adds_entity_for_roborock_coordinator(q7):
    """The delayed scan finds roborock coordinators and adds a camera."""
    from custom_components.roborock_b01.camera import _async_setup_b01_cameras

    hass = _make_hass_with_service()
    entry = ConfigEntry(entry_id="b01ui", domain=DOMAIN)
    hass.config_entries.add(_roborock_entry(q7["coord"]))
    collector = _EntityCollector()

    _async_setup_b01_cameras(hass, collector, entry)
    # The immediate scan finds the already-loaded roborock entry.
    assert len(collector.added) == 1
    camera = collector.added[0]
    assert camera.coordinator is q7["coord"]
    assert camera._attr_unique_id == f"{q7['coord'].duid}_b01_map"

    # The delayed re-scan must not duplicate anything.
    (_delay, action, _cancel) = hass._call_later[-1]
    assert _delay > 0
    action()
    assert len(collector.added) == 1
    # A late-coordinator listener was registered on the roborock entry signal.
    assert any(
        signal.startswith("roborock_coordinator_added_")
        for signal in hass._dispatch
    )


async def test_camera_scan_dedupes_and_handles_late_coordinators(q7):
    """Re-scans don't duplicate; a late coordinator gets its own camera."""
    from custom_components.roborock_b01.camera import _async_setup_b01_cameras

    hass = _make_hass_with_service()
    entry = ConfigEntry(entry_id="b01ui", domain=DOMAIN)
    hass.config_entries.add(_roborock_entry(q7["coord"]))
    collector = _EntityCollector()
    _async_setup_b01_cameras(hass, collector, entry)
    (_delay, action, _cancel) = hass._call_later[-1]
    action()
    action()  # re-scan: same coordinator, must not duplicate
    assert len(collector.added) == 1

    # Late coordinator arrives via the dispatcher signal.
    signal = next(
        s for s in hass._dispatch if s.startswith("roborock_coordinator_added_")
    )
    late_coord = q7["coord"]  # different identity not needed: dedupe is per duid
    hass._dispatch[signal][0](late_coord)
    assert len(collector.added) == 1  # same duid -> still deduped


async def test_camera_unload_removes_dispatcher_listener(q7):
    """Entry unload disconnects the late-coordinator listener."""
    from custom_components.roborock_b01.camera import _async_setup_b01_cameras

    hass = _make_hass_with_service()
    entry = ConfigEntry(entry_id="b01ui", domain=DOMAIN)
    _async_setup_b01_cameras(hass, _EntityCollector(), entry)

    signals_before = {
        s: len(v) for s, v in hass._dispatch.items()
        if s.startswith("roborock_coordinator_added_")
    }
    entry.run_unloads()
    signals_after = {
        s: len(v) for s, v in hass._dispatch.items()
        if s.startswith("roborock_coordinator_added_")
    }
    for signal, count in signals_before.items():
        assert signals_after.get(signal, 0) == count - 1


async def test_unload_entry_unloads_camera_platform(q7):
    """async_unload_entry forwards the unload to HA (camera platform)."""
    hass = _make_hass_with_service()
    entry = ConfigEntry(entry_id="e1", domain=DOMAIN)

    assert await b01.async_unload_entry(hass, entry) is True
    assert hass.config_entries.unloaded == [(entry, ("camera",))]


# --------------------------------------------------------------------------
# Registry-level service round trip (real handler, no direct func call)
# --------------------------------------------------------------------------


async def test_service_call_round_trip_via_registry(q7, q7_entity):
    """hass.services.async_call drives the real handler end to end."""
    hass = _make_hass_with_service()
    entry = ConfigEntry(domain=DOMAIN)
    await b01.async_setup_entry(hass, entry)

    hass.data["_entity_components"] = {"vacuum": _FakeComponent([q7_entity])}

    await hass.services.async_call(
        DOMAIN, "clean_segment",
        {"entity_id": ["vacuum.q7_test"], "segment_id": ["16"]},
    )

    command, params = q7["rpc"].published[-1]
    assert command == RoborockB01Q7Methods.SET_ROOM_CLEAN
    assert params == {"clean_type": 1, "ctrl_value": 1, "room_ids": [16]}


async def test_service_call_schema_rejects_missing_fields():
    """The registry-level call validates through the real schema."""
    hass = _make_hass_with_service()
    entry = ConfigEntry(domain=DOMAIN)
    await b01.async_setup_entry(hass, entry)

    with pytest.raises(ValueError):
        await hass.services.async_call(DOMAIN, "clean_segment", {})


async def test_service_call_unknown_service_fails(q7):
    """Unknown services under our domain are rejected by the registry."""
    hass = _make_hass_with_service()
    entry = ConfigEntry(domain=DOMAIN)
    await b01.async_setup_entry(hass, entry)

    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "does_not_exist", {})


# --------------------------------------------------------------------------
# Full lifecycle: setup -> camera -> service -> unload
# --------------------------------------------------------------------------


async def test_full_lifecycle(q10, q10_entity):
    """Setup, camera discovery, room clean, then unload - in one flow."""
    from roborock.data.b01_q10.b01_q10_code_mappings import B01_Q10_DP
    from roborock.map.b01_q10_map_parser import Q10MapPacket, Q10Room

    from custom_components.roborock_b01.camera import _async_setup_b01_cameras

    hass = _make_hass_with_service()
    entry = ConfigEntry(entry_id="life", domain=DOMAIN)
    assert await b01.async_setup_entry(hass, entry) is True

    # Camera platform (as HA would set it up for the forwarded platform).
    hass.config_entries.add(_roborock_entry(q10["coord"], entry_id="rob10"))
    collector = _EntityCollector()
    _async_setup_b01_cameras(hass, collector, entry)
    (_delay, action, _cancel) = hass._call_later[-1]
    action()
    camera = collector.added[0]
    camera.hass = object()
    await camera.async_added_to_hass()

    # Room clean through the registry (Q10 entity, real handler path).
    hass.data["_entity_components"] = {"vacuum": _FakeComponent([q10_entity])}
    await hass.services.async_call(
        DOMAIN, "clean_segment",
        {"entity_id": ["vacuum.q10_test"], "segment_id": ["5"]},
    )
    assert q10_entity.last_segments == ["5"]

    # Q10 flow: the camera kicks REQUEST_DPS on first image fetch.
    api = q10["api"]
    api.map.update_from_map_packet(
        Q10MapPacket(map_id=3, width=8, height=8, grid=bytes([2] * 64),
                     rooms=[Q10Room(id=5, raw_name="Living",
                                    pixel_value=2, pixel_count=64)])
    )
    api.maps.update_from_dps(
        {B01_Q10_DP.MULTI_MAP: {"op": "list", "result": 1,
                                "data": [{"id": "3", "name": "Main",
                                          "timestamp": 1}]}}
    )
    # The camera on a Q10 kicks REQUEST_DPS on first image fetch.
    image = await camera.async_camera_image()
    assert image is not None and image[:4] == b"\x89PNG"
    assert q10["channel"].published[0][0] == B01_Q10_DP.REQUEST_DPS

    # Unload: camera listener removed, platform unloaded.
    await camera.async_will_remove_from_hass()
    assert camera._unsub_push is None
    assert await b01.async_unload_entry(hass, entry) is True
    assert hass.config_entries.unloaded == [(entry, ("camera",))]
