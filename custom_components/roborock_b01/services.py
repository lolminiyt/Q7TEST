"""Services for the Roborock B01 integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_component import DATA_INSTANCES

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_CLEAN_SEGMENT = "clean_segment"

SERVICE_CLEAN_SEGMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required("segment_id"): cv.ensure_list,
    }
)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the B01 services (idempotent; re-registering replaces)."""

    async def _async_handle_clean_segment(call: ServiceCall) -> None:
        """Clean rooms by segment (room) id on the targeted vacuum(s)."""
        from homeassistant.components.vacuum import (
            DOMAIN as VACUUM_DOMAIN,
            VacuumEntityFeature,
        )

        component = hass.data.get(DATA_INSTANCES, {}).get(VACUUM_DOMAIN)
        if component is None:
            raise HomeAssistantError("The vacuum integration is not set up")
        entities = await component.async_extract_from_service(call)
        for entity in entities:
            features = entity.supported_features
            if not features & VacuumEntityFeature.CLEAN_AREA:
                raise HomeAssistantError(
                    f"{entity.entity_id} does not support room cleaning"
                )
            await entity.async_clean_segments(call.data["segment_id"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAN_SEGMENT,
        _async_handle_clean_segment,
        schema=SERVICE_CLEAN_SEGMENT_SCHEMA,
    )
