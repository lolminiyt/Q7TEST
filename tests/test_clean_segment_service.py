"""Tests for the clean_segment service and the CLEAN_AREA feature flag.

Runs the real component code (services.py, vacuum.py patches) against real
python-roborock traits over fake channels; only Home Assistant is stubbed.
"""

from __future__ import annotations

import pytest
from ha_stubs import FakeHass
from homeassistant.components.roborock.vacuum import (
    RoborockQ7Vacuum,
    RoborockQ10Vacuum,
)
from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from roborock.roborock_typing import RoborockB01Q7Methods

from custom_components.roborock_b01.services import (
    SERVICE_CLEAN_SEGMENT,
    async_register_services,
)


@pytest.fixture
def hass_with_service():
    """A FakeHass with the B01 services registered."""
    hass = FakeHass()
    async_register_services(hass)
    return hass


class _FakeComponent:
    """Mirror of EntityComponent.async_extract_from_service for tests."""

    def __init__(self, entities):
        self._entities = entities

    async def async_extract_from_service(self, call):
        wanted = call.data["entity_id"]
        return [e for e in self._entities if e.entity_id in wanted]


def _call(entity_ids, segment_ids):
    call = type("Call", (), {})()
    call.data = {"entity_id": entity_ids, "segment_id": segment_ids}
    return call


# --------------------------------------------------------------------------
# Service registration
# --------------------------------------------------------------------------


def test_service_registered(hass_with_service):
    """The service is registered with its validation schema."""
    registered = hass_with_service.services.get("roborock_b01", {})
    assert SERVICE_CLEAN_SEGMENT in registered
    func, schema = registered[SERVICE_CLEAN_SEGMENT]
    assert callable(func)
    # Schema validates: entity_id + segment_id are required.
    with pytest.raises(ValueError):
        schema({"entity_id": ["vacuum.q7_test"]})
    with pytest.raises(ValueError):
        schema({"segment_id": ["16"]})


# --------------------------------------------------------------------------
# Entity resolution + Q7 wire payload
# --------------------------------------------------------------------------


async def test_clean_segment_q7_wire_payload(q7, q7_entity, hass_with_service):
    """The handler calls the entity directly and sends the exact Q7 command."""
    component = _FakeComponent([q7_entity])
    hass_with_service.data["_entity_components"] = {"vacuum": component}

    func, _ = hass_with_service.services["roborock_b01"][SERVICE_CLEAN_SEGMENT]
    await func(_call(["vacuum.q7_test"], ["16", "17"]))

    # The real Q7 trait sent the library-verified room-clean payload.
    command, params = q7["rpc"].published[-1]
    assert command == RoborockB01Q7Methods.SET_ROOM_CLEAN
    assert params == {"clean_type": 1, "ctrl_value": 1, "room_ids": [16, 17]}


async def test_clean_segment_q10_routes_to_entity(q10_entity, hass_with_service):
    """Q10 room cleaning is core's; the service delegates to the entity."""
    component = _FakeComponent([q10_entity])
    hass_with_service.data["_entity_components"] = {"vacuum": component}

    func, _ = hass_with_service.services["roborock_b01"][SERVICE_CLEAN_SEGMENT]
    await func(_call(["vacuum.q10_test"], ["5"]))

    assert q10_entity.last_segments == ["5"]


async def test_clean_segment_non_clean_area_entity_rejected(
    q7_entity, hass_with_service
):
    """An entity without CLEAN_AREA is refused with a clear error."""
    # Per-instance feature override (the patched property must honor it).
    q7_entity._attr_supported_features = VacuumEntityFeature(0)

    component = _FakeComponent([q7_entity])
    hass_with_service.data["_entity_components"] = {"vacuum": component}

    func, _ = hass_with_service.services["roborock_b01"][SERVICE_CLEAN_SEGMENT]
    with pytest.raises(HomeAssistantError, match="room cleaning"):
        await func(_call(["vacuum.q7_test"], ["16"]))


async def test_clean_segment_bad_ids_validation(q7_entity):
    """Non-numeric segment ids raise a validation error before any command."""
    with pytest.raises(ServiceValidationError):
        await q7_entity.async_clean_segments(["kitchen"])


async def test_clean_segment_device_error_mapped(q7, q7_entity, hass_with_service):
    """A device-level failure becomes a translated HomeAssistantError."""
    q7["rpc"].fail_next = True
    component = _FakeComponent([q7_entity])
    hass_with_service.data["_entity_components"] = {"vacuum": component}

    func, _ = hass_with_service.services["roborock_b01"][SERVICE_CLEAN_SEGMENT]
    with pytest.raises(HomeAssistantError) as exc_info:
        await func(_call(["vacuum.q7_test"], ["16"]))
    assert exc_info.value.translation_key == "command_failed"


# --------------------------------------------------------------------------
# CLEAN_AREA advertised regardless of setup order
# --------------------------------------------------------------------------


def test_q7_clean_area_order_independent(q7):
    """CLEAN_AREA shows up even on entities that cached flags pre-patch.

    Simulates the bad order: core creates + caches supported_features
    (non-data cached_property) BEFORE roborock_b01 loads. The class-level
    property patch must defeat the stale per-instance cache.
    """
    from propcache.api import cached_property

    from custom_components.roborock_b01.vacuum import patch_b01_vacuum_classes

    cls = RoborockQ7Vacuum
    saved_property = cls.__dict__["supported_features"]
    saved_attr = cls._attr_supported_features
    try:
        # Simulate core's original cached_property and flags (no CLEAN_AREA).
        cls._attr_supported_features = saved_attr & ~VacuumEntityFeature.CLEAN_AREA

        def _original_features(self):
            return self._attr_supported_features

        cached = cached_property(_original_features)
        cached.__set_name__(cls, "supported_features")
        cls.supported_features = cached
        cls._b01_clean_area = False

        # Entity created and cached BEFORE the integration loads.
        pre = RoborockQ7Vacuum(q7["coord"])
        assert not pre.supported_features & VacuumEntityFeature.CLEAN_AREA

        # Integration loads and patches.
        patch_b01_vacuum_classes()

        # The stale per-instance cache is defeated; fresh entities agree.
        assert pre.supported_features & VacuumEntityFeature.CLEAN_AREA
        assert (
            RoborockQ7Vacuum(q7["coord"]).supported_features
            & VacuumEntityFeature.CLEAN_AREA
        )
    finally:
        # Restore the session-patched state for other tests.
        cls.supported_features = saved_property
        cls._attr_supported_features = saved_attr
        cls._b01_clean_area = True


def test_q7_supported_features_honors_instance_override(q7_entity):
    """The patched property must not clobber per-instance overrides."""
    q7_entity._attr_supported_features = VacuumEntityFeature.PAUSE
    assert q7_entity.supported_features == VacuumEntityFeature.PAUSE


def test_q10_clean_area_asserted(q10):
    """Core Q10 keeps CLEAN_AREA (the service's capability check depends on it)."""
    entity = RoborockQ10Vacuum(q10["coord"])
    assert entity.supported_features & VacuumEntityFeature.CLEAN_AREA
