"""Roborock B01 (Q7 / Q10) full support package.

Built from python-roborock docs/DEVICES.md, B01 protocol section:

- B01 (pv=B01) devices are MQTT-only (no local TCP), DPS protocol,
  one shared MqttSession, per-device MqttChannel + helpers
  (b01_q7_channel.py / b01_q10_channel.py).
- Q7: device.b01_q7_properties. The device streams full map frames on its
  own while cleaning, so the rendered map stays current without polling:
  register map_content.add_update_listener(cb) and read
  image_content / map_data when notified.
- Q10: device.b01_q10_properties with vacuum.* commands and
  command.send() for raw DP commands.
- DeviceManager (owned by the HA core roborock integration) detects pv
  automatically.

NO extra login: this package reuses the already-authenticated HA core
Roborock session (config entry runtime_data coordinators). The official
Roborock integration must be set up first.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "roborock_b01"

CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): {}}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up B01 vacuum patches + live map cameras."""
    from .vacuum import patch_b01_vacuum_classes

    patched = patch_b01_vacuum_classes()
    _LOGGER.info("roborock_b01: vacuum patches applied: %s", patched)

    # Camera platform creates push-driven B01 map cameras (see camera.py).
    await async_load_platform(hass, "camera", DOMAIN, {}, config)
    return True
