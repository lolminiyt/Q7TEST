"""Stub Home Assistant modules so component tests run without HA installed.

Only the surface roborock_b01 touches is implemented, mirroring real HA
semantics: propcache cached_property-backed supported_features, the
service registry, EntityComponent entity lookup, Camera base class, and
the exception/voluptuous constructors the component uses. The real
python-roborock library is NOT stubbed.
"""

from __future__ import annotations

import sys
import types
from enum import IntFlag

from propcache.api import cached_property


# ---------------------------------------------------------------- enums
class VacuumEntityFeature(IntFlag):
    PAUSE = 1
    STOP = 2
    RETURN_HOME = 4
    FAN_SPEED = 8
    SEND_COMMAND = 16
    LOCATE = 32
    CLEAN_SPOT = 64
    START = 128
    CLEAN_AREA = 256


# ------------------------------------------------------- core exceptions
class _TranslationError(Exception):
    def __init__(self, *args, translation_domain=None, translation_key=None, translation_placeholders=None, **kw):
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders or {}


class HomeAssistantError(_TranslationError):
    pass


class ServiceValidationError(_TranslationError):
    pass


# ------------------------------------------------------------ voluptuous
class _Required:
    def __init__(self, key):
        self.key = key


class _Optional:
    def __init__(self, key):
        self.key = key


class _SchemaStub:
    def __init__(self, schema):
        self.schema = schema
        self.required_keys = [
            k.key for k in schema if isinstance(k, _Required)
        ]

    def __call__(self, data):
        missing = [k for k in self.required_keys if k not in data]
        if missing:
            raise ValueError(f"required key(s) not provided: {', '.join(missing)}")
        return data


class _VoluptuousStub:
    Optional = _Optional
    Required = _Required

    @staticmethod
    def Schema(schema, extra=None):
        return _SchemaStub(schema)


# ------------------------------------------------- module tree injection
def _module(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__package__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class _FakeServiceRegistry(dict):
    """domain -> {name: (handler, schema)} with async_call validation."""

    def __init__(self, hass):
        super().__init__()
        self._hass = hass

    def async_register(self, domain, service, func, schema=None):
        self.setdefault(domain, {})[service] = (func, schema)

    async def async_call(self, domain, service, data=None, blocking=False):
        handler = self.get(domain, {}).get(service)
        if handler is None:
            raise HomeAssistantError(f"Service {domain}.{service} not found")
        func, schema = handler
        if schema is not None:
            data = schema(data or {})
        return await func(types.SimpleNamespace(hass=self._hass, data=data or {}))


class ConfigEntry:
    """Minimal config entry: domain, runtime_data, unload callbacks."""

    def __init__(self, entry_id="entry", domain=None, runtime_data=None):
        self.entry_id = entry_id
        self.domain = domain
        self.runtime_data = runtime_data
        self._on_unload = []

    def async_on_unload(self, callback):
        self._on_unload.append(callback)

    def run_unloads(self):
        """Fire (and clear) pending unload callbacks like real unload does."""
        callbacks, self._on_unload = self._on_unload, []
        for cb in callbacks:
            cb()


class _FakeConfigEntries:
    """hass.config_entries: store, forward, unload - all recorded."""

    def __init__(self):
        self._entries = []
        self.forwarded = []
        self.unloaded = []

    def add(self, entry):
        self._entries.append(entry)

    def async_entries(self, domain=None):
        if domain is None:
            return list(self._entries)
        return [e for e in self._entries if getattr(e, "domain", None) == domain]

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry, tuple(platforms)))
        return True

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append((entry, tuple(platforms)))
        return True


class FakeHass:
    """Minimal hass: data dict, service registry, bus, states."""

    def __init__(self):
        self.data = {}
        self.services = _FakeServiceRegistry(self)
        self.states = types.SimpleNamespace(get=lambda entity_id: None)
        self.bus = types.SimpleNamespace(
            async_listen_once=lambda *a, **k: (lambda: None)
        )
        self._dispatch = {}
        self.config_entries = _FakeConfigEntries()
        self._call_later = []  # (delay, action, cancel)
        self._platform_loads = []  # async_load_platform (component, domain)


# ------------------------------------------------------- entity helpers
class Segment:
    """Mirror of homeassistant.components.vacuum.Segment."""

    def __init__(self, id, name, group=None):
        self.id = id
        self.name = name
        self.group = group


class _EntityBase:
    _attr_has_entity_name = True

    def __init__(self):
        self.hass = None
        self.entity_id = None
        self.state_write_count = 0

    @cached_property
    def supported_features(self):
        """Mirror real HA: cached on first read (non-data descriptor)."""
        return self._attr_supported_features

    @property
    def name(self):
        return self._attr_name

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    @property
    def available(self):
        return True

    def async_write_ha_state(self):
        self.state_write_count += 1

    def schedule_update_ha_state(self, force_refresh=False):
        self.state_write_count += 1


class _CameraBase(_EntityBase):
    def __init__(self):
        super().__init__()
        self._attr_should_poll = False

    @property
    def supported_features(self):  # real Camera has no feature caching
        return None


class _DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(kwargs)


# ------------------------------------------------------------- vacuum UI
class RoborockQ7Vacuum(_EntityBase):
    """Stub of HA core's Q7 vacuum: cached-property features, gap methods."""

    _attr_supported_features = (
        VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.SEND_COMMAND
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.START
    )

    def __init__(self, coordinator):
        super().__init__()
        self.coordinator = coordinator
        self.entity_id = "vacuum.q7_test"

    async def get_maps(self):
        raise HomeAssistantError("not implemented in core stub")

    async def async_clean_segments(self, segment_ids, **kwargs):
        raise HomeAssistantError("not implemented in core stub")

    async def get_vacuum_current_position(self):
        raise HomeAssistantError("not implemented in core stub")

    async def async_set_vacuum_goto_position(self, x, y):
        raise HomeAssistantError("not implemented in core stub")

    async def async_set_vacuum_zoned_cleaning(self, x1, y1, x2, y2, repeats):
        raise HomeAssistantError("not implemented in core stub")


class RoborockQ10Vacuum(_EntityBase):
    """Stub of HA core's Q10 vacuum (goto/zone/segments exist in core)."""

    _attr_supported_features = (
        VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.SEND_COMMAND
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.START
        | VacuumEntityFeature.CLEAN_AREA
    )

    def __init__(self, coordinator):
        super().__init__()
        self.coordinator = coordinator
        self.entity_id = "vacuum.q10_test"

    async def get_maps(self):
        raise HomeAssistantError("not implemented in core stub")

    async def async_clean_segments(self, segment_ids, **kwargs):
        # Core Q10 implements this; record for assertions.
        self.last_segments = segment_ids


# ------------------------------------------------------------- install
def install() -> None:
    """Idempotently inject the fake HA module tree into sys.modules."""
    if "homeassistant" in sys.modules and getattr(
        sys.modules["homeassistant"], "_b01_stub", False
    ):
        return

    roborock_ui = _module(
        "homeassistant.components.roborock.vacuum",
        RoborockQ7Vacuum=RoborockQ7Vacuum,
        RoborockQ10Vacuum=RoborockQ10Vacuum,
    )
    _module("homeassistant.components", roborock=None)
    _module(
        "homeassistant.components.roborock",
        vacuum=roborock_ui,
    )
    _module(
        "homeassistant.components.vacuum",
        Segment=Segment,
        DOMAIN="vacuum",
        VacuumEntityFeature=VacuumEntityFeature,
    )
    _module("homeassistant.components.camera", Camera=_CameraBase)

    _module(
        "homeassistant.core",
        HomeAssistant=FakeHass,
        ServiceCall=type("ServiceCall", (), {}),
        callback=lambda f: f,
    )
    _module("homeassistant.config_entries", ConfigEntry=ConfigEntry)
    _module("homeassistant.helpers.typing", ConfigType=dict, DiscoveryInfoType=dict)
    async def _async_load_platform(hass, component, domain, *args):
        hass._platform_loads.append((component, domain))

    _module("homeassistant.helpers.discovery", async_load_platform=_async_load_platform)
    _module(
        "homeassistant.helpers",
        config_validation=types.SimpleNamespace(
            entity_ids="<cv-entity_ids>",
            ensure_list=lambda v: v if isinstance(v, list) else [v],
        ),
    )
    _module("homeassistant.helpers.entity_component", DATA_INSTANCES="_entity_components")
    _module("homeassistant.helpers.device_registry", DeviceInfo=_DeviceInfo)
    def _async_dispatcher_connect(hass, signal, target):
        listeners = hass._dispatch.setdefault(signal, [])
        listeners.append(target)

        def _remove():
            if target in listeners:
                listeners.remove(target)

        return _remove

    _module(
        "homeassistant.helpers.dispatcher",
        async_dispatcher_connect=_async_dispatcher_connect,
    )
    _module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    def _async_call_later(hass, delay, action):
        cancel = lambda: None
        hass._call_later.append((delay, action, cancel))
        return cancel

    _module(
        "homeassistant.helpers.event",
        async_call_later=_async_call_later,
    )
    _module(
        "homeassistant.exceptions",
        HomeAssistantError=HomeAssistantError,
        ServiceValidationError=ServiceValidationError,
    )
    _module("voluptuous", Optional=_Optional, Required=_Required, Schema=_VoluptuousStub.Schema)
    import voluptuous as _vol  # the module just installed

    _vol.ALLOW_EXTRA = "allow_extra"

    ha = sys.modules["homeassistant"] = types.ModuleType("homeassistant")
    ha._b01_stub = True
    ha.__package__ = "homeassistant"
