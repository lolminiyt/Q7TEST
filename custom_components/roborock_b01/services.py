"""Services for the Roborock B01 integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_component import DATA_INSTANCES

from .const import DOMAIN
from .failsafe import async_with_retry

_LOGGER = logging.getLogger(__name__)

SERVICE_CLEAN_SEGMENT = "clean_segment"
SERVICE_CLEAN_SETTINGS = "clean_settings"
# Core-parity entity services (HA core roborock exposes these under the
# roborock domain; standalone registers them under our own).
SERVICE_GET_MAPS = "get_maps"
SERVICE_CURRENT_POSITION = "get_vacuum_current_position"
SERVICE_GOTO_POSITION = "set_vacuum_goto_position"
SERVICE_ZONED_CLEANING = "set_vacuum_zoned_cleaning"

SERVICE_CLEAN_SEGMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required("segment_id"): cv.ensure_list,
    }
)

# Optional clean-settings fields; each maps to a verified Q7PropertiesApi
# setter and the enum whose keys are the accepted values. Values are
# validated at call time with the same enums the library ships.
_CLEAN_SETTINGS_FIELDS = {
    "fan_speed": "set_fan_speed",  # quiet balanced turbo max max_plus
    "water_level": "set_water_level",  # low medium high
    "clean_mode": "set_mode",  # vacuum vac_and_mop mop
    "repeat": "set_repeat_state",  # one two
    "clean_route": "set_clean_path_preference",  # balanced deep
}

SERVICE_CLEAN_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Optional("fan_speed"): cv.string,
        vol.Optional("water_level"): cv.string,
        vol.Optional("clean_mode"): cv.string,
        vol.Optional("repeat"): cv.string,
        vol.Optional("clean_route"): cv.string,
    }
)

SERVICE_GOTO_POSITION_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required("x"): vol.Coerce(int),
        vol.Required("y"): vol.Coerce(int),
    }
)

SERVICE_ZONED_CLEANING_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required("x1"): vol.Coerce(int),
        vol.Required("y1"): vol.Coerce(int),
        vol.Required("x2"): vol.Coerce(int),
        vol.Required("y2"): vol.Coerce(int),
        vol.Required("repeats"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
    }
)


async def _async_target_vacuum_entities(hass: HomeAssistant, call: ServiceCall):
    """Resolve the targeted vacuum entity objects (real EntityComponent)."""
    from homeassistant.components.vacuum import DOMAIN as VACUUM_DOMAIN

    component = hass.data.get(DATA_INSTANCES, {}).get(VACUUM_DOMAIN)
    if component is None:
        raise HomeAssistantError("The vacuum integration is not set up")
    return await component.async_extract_from_service(call)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the B01 services (idempotent; re-registering replaces)."""

    async def _async_handle_clean_segment(call: ServiceCall) -> None:
        """Clean rooms by segment (room) id on the targeted vacuum(s)."""
        from homeassistant.components.vacuum import VacuumEntityFeature

        for entity in await _async_target_vacuum_entities(hass, call):
            features = entity.supported_features
            if not features & VacuumEntityFeature.CLEAN_AREA:
                raise HomeAssistantError(
                    f"{entity.entity_id} does not support room cleaning"
                )
            await entity.async_clean_segments(call.data["segment_id"])

    async def _async_handle_clean_settings(call: ServiceCall) -> None:
        """Apply suction/water/mode/cycles/route settings to the vacuum(s)."""
        from roborock.data.b01_q7.b01_q7_code_mappings import (
            CleanPathPreferenceMapping,
            CleanRepeatMapping,
            CleanTypeMapping,
            SCWindMapping,
            WaterLevelMapping,
        )
        from roborock.exceptions import RoborockException

        mappings = {
            "fan_speed": SCWindMapping,
            "water_level": WaterLevelMapping,
            "clean_mode": CleanTypeMapping,
            "repeat": CleanRepeatMapping,
            "clean_route": CleanPathPreferenceMapping,
        }
        requested = {
            field: call.data[field]
            for field in _CLEAN_SETTINGS_FIELDS
            if field in call.data
        }
        if not requested:
            raise ServiceValidationError(
                "Provide at least one of: " + ", ".join(sorted(_CLEAN_SETTINGS_FIELDS))
            )

        def _invalid(field: str, value: object, mapping) -> ServiceValidationError:
            return ServiceValidationError(
                f"{field} '{value}' is not valid. Use one of: "
                f"{', '.join(mapping.keys())}"
            )

        resolved: dict[str, object] = {}
        for field, value in requested.items():
            if not isinstance(value, str):
                raise ServiceValidationError(
                    f"{field} must be a string, one of: "
                    f"{', '.join(mappings[field].keys())}"
                )
            try:
                resolved[field] = mappings[field].from_value(value)
            except ValueError as err:
                raise _invalid(field, value, mappings[field]) from err
            except KeyError as err:
                raise _invalid(field, value, mappings[field]) from err

        for entity in await _async_target_vacuum_entities(hass, call):
            coordinator = getattr(entity, "coordinator", None)
            api = getattr(coordinator, "api", None)
            if api is None or not hasattr(api, "set_fan_speed"):
                raise HomeAssistantError(
                    f"{entity.entity_id}: clean_settings is only available "
                    "on B01 Q7 vacuums (the Q10 has no setters in "
                    "python-roborock yet)"
                )
            applied_any = False
            for field, value in resolved.items():
                try:
                    await async_with_retry(
                        f"set_{field}",
                        getattr(api, _CLEAN_SETTINGS_FIELDS[field]),
                        value,
                    )
                except RoborockException as err:
                    raise HomeAssistantError(
                        f"{entity.entity_id}: the device rejected "
                        f"{field}={requested[field]}"
                    ) from err
                applied_any = True
            if applied_any:
                # The vacuum UI reads coordinator.data (wind_name, ...); a
                # write alone leaves it stale for up to a minute.
                await coordinator.async_refresh()

    async def _async_handle_get_maps(call: ServiceCall) -> dict:
        """Current map id + room ids/names (response-only service)."""
        results = {}
        for entity in await _async_target_vacuum_entities(hass, call):
            results[entity.entity_id] = await entity.get_maps()
        return results

    async def _async_handle_current_position(call: ServiceCall) -> dict:
        """Robot x/y (map pixel coordinates) for the targeted vacuum(s)."""
        results = {}
        for entity in await _async_target_vacuum_entities(hass, call):
            results[entity.entity_id] = await entity.get_vacuum_current_position()
        return results

    async def _async_handle_goto_position(call: ServiceCall) -> None:
        """Q10 only: drive the robot to a map coordinate."""
        from homeassistant.exceptions import ServiceNotSupported

        for entity in await _async_target_vacuum_entities(hass, call):
            if not hasattr(entity, "async_set_vacuum_goto_position"):
                raise ServiceNotSupported(
                    DOMAIN, SERVICE_GOTO_POSITION, entity.entity_id
                )
            await entity.async_set_vacuum_goto_position(call.data["x"], call.data["y"])

    async def _async_handle_zoned_cleaning(call: ServiceCall) -> None:
        """Q10 only: clean a rectangular zone, optionally repeated."""
        from homeassistant.exceptions import ServiceNotSupported

        for entity in await _async_target_vacuum_entities(hass, call):
            if not hasattr(entity, "async_set_vacuum_zoned_cleaning"):
                raise ServiceNotSupported(
                    DOMAIN, SERVICE_ZONED_CLEANING, entity.entity_id
                )
            await entity.async_set_vacuum_zoned_cleaning(
                call.data["x1"],
                call.data["y1"],
                call.data["x2"],
                call.data["y2"],
                call.data["repeats"],
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAN_SEGMENT,
        _async_handle_clean_segment,
        schema=SERVICE_CLEAN_SEGMENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAN_SETTINGS,
        _async_handle_clean_settings,
        schema=SERVICE_CLEAN_SETTINGS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MAPS,
        _async_handle_get_maps,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CURRENT_POSITION,
        _async_handle_current_position,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GOTO_POSITION,
        _async_handle_goto_position,
        schema=SERVICE_GOTO_POSITION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ZONED_CLEANING,
        _async_handle_zoned_cleaning,
        schema=SERVICE_ZONED_CLEANING_SCHEMA,
    )
