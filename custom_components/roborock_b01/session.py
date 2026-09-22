"""Standalone Roborock session management.

Credentials are captured once at config time: the email-code config
flow stores the login result (``user_data``) in the config entry, and
this module reuses it directly - no password re-login on start, which
also keeps the session pointed at the same server (the Roborock cloud)
that minted it.

MQTT credentials can still expire while HA runs; the library reports
that as ``MqttSessionUnauthorized`` at setup (mapped to
``ConfigEntryAuthFailed``, which starts HA's reauth flow) or fires the
``mqtt_session_unauthorized_hook`` on an established connection (starts
reauth for the entry directly). HA calls ``close()`` on shutdown/unload.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from roborock.data import UserData
from roborock.devices.device import RoborockDevice
from roborock.devices.device_manager import (
    DeviceManager,
    UserParams,
    create_device_manager,
)
from roborock.exceptions import RoborockException, RoborockInvalidCredentials
from roborock.mqtt.session import MqttSessionUnauthorized

from .const import CONF_BASE_URL, CONF_USER_DATA

_LOGGER = logging.getLogger(__name__)


class RoborockB01Session:
    """A logged-in cloud session with all its connected devices."""

    def __init__(
        self, username: str, user_data: UserData, base_url: str | None
    ) -> None:
        """Wrap credentials; call setup() to connect."""
        self.username = username
        self.user_data = user_data
        self.base_url = base_url
        self.device_manager: DeviceManager | None = None

    async def setup(
        self,
        hass: HomeAssistant | None = None,
        entry: ConfigEntry | None = None,
    ) -> list[RoborockDevice]:
        """Create the device manager and connect every device.

        ``hass`` + ``entry`` wire the MQTT-unauthorized hook that starts
        HA's reauth flow; without them (tests, offline tooling) the
        session still connects but expiry surfaces as
        ``ConfigEntryAuthFailed`` instead of an interactive reauth.
        """

        def start_reauth() -> None:
            if hass is not None and entry is not None:
                entry.async_start_reauth(hass)

        unauthorized_hook: Callable[[], None] | None = (
            start_reauth if hass is not None and entry is not None else None
        )
        try:
            self.device_manager = await create_device_manager(
                UserParams(
                    username=self.username,
                    user_data=self.user_data,
                    base_url=self.base_url or None,
                ),
                mqtt_session_unauthorized_hook=unauthorized_hook,
            )
            devices = await self.device_manager.discover_devices()
        except MqttSessionUnauthorized as err:
            raise ConfigEntryAuthFailed(
                f"Roborock MQTT rejected the stored credentials: {err}"
            ) from err
        except RoborockInvalidCredentials as err:
            raise ConfigEntryAuthFailed(f"Roborock credentials expired: {err}") from err
        except RoborockException as err:
            raise ConfigEntryNotReady(f"Roborock connection failed: {err}") from err
        for device in devices:
            _LOGGER.info(
                "roborock_b01: connected device %s (%s, pv=%s)",
                device.name,
                device.product.model,
                device.device_info.pv,
            )
        return devices

    async def b01_devices(self) -> list[RoborockDevice]:
        """The Q7/Q10 (pv=B01) devices this account owns."""
        manager = self.device_manager
        if manager is None:
            return []
        devices = []
        for device in await manager.get_devices():
            if getattr(device.device_info, "pv", "") == "B01":
                devices.append(device)
        return devices

    async def close(self) -> None:
        """Drop MQTT connections."""
        if self.device_manager is not None:
            await self.device_manager.close()
            self.device_manager = None


async def async_create_session(
    hass: HomeAssistant, entry: ConfigEntry
) -> RoborockB01Session:
    """Build a session from the credentials stored in the config entry.

    Expiry heals through the reauth flow, not a re-login: MQTT
    rejections map to ``ConfigEntryAuthFailed`` / the unauthorized
    hook, both of which restart the email-code flow for this entry.
    """
    session = RoborockB01Session(
        username=entry.data[CONF_USERNAME],
        user_data=user_data_from_entry(entry),
        base_url=entry.data.get(CONF_BASE_URL),
    )
    await session.setup(hass=hass, entry=entry)
    return session


def user_data_from_entry(entry: ConfigEntry) -> UserData:
    """Deserialize the stored UserData blob."""
    return UserData.from_dict(entry.data[CONF_USER_DATA])
