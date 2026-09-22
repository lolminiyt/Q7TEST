"""Config flow: standalone Roborock email-code login.

Mirrors the official integration's login path (``request_code_v4`` ->
``code_login_v4``). python-roborock 7.8.1's ``pass_login_v3`` always
raises ``NotImplementedError``, so the emailed verification code is
the only login path that works; no password is ever stored.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME
from homeassistant.core import callback
from roborock.exceptions import (
    RoborockAccountDoesNotExist,
    RoborockException,
    RoborockInvalidCode,
    RoborockInvalidEmail,
    RoborockTooFrequentCodeRequests,
    RoborockUrlException,
)
from roborock.web_api import RoborockApiClient

from .const import (
    CONF_BASE_URL,
    CONF_ENTRY_CODE,
    CONF_USER_DATA,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class RoborockB01ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle standalone setup for Roborock B01."""

    VERSION = 1

    _username: str
    _client: RoborockApiClient

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Email -> request the login code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()
            self._username = username
            self._client = RoborockApiClient(
                username=username, base_url=None, session=None
            )
            errors = await self._request_code()
            if not errors:
                return await self.async_step_code()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_USERNAME): str}),
            errors=errors,
        )

    async def _request_code(self) -> dict[str, str]:
        """Email the verification code; map failures to flow errors."""
        try:
            await self._client.request_code_v4()
        except RoborockAccountDoesNotExist:
            return {"base": "invalid_email"}
        except RoborockUrlException:
            return {"base": "unknown_url"}
        except RoborockInvalidEmail:
            return {"base": "invalid_email_format"}
        except RoborockTooFrequentCodeRequests:
            return {"base": "too_frequent_code_requests"}
        except RoborockException:
            _LOGGER.exception("roborock_b01: unexpected error requesting code")
            return {"base": "unknown_roborock"}
        except Exception:
            _LOGGER.exception("roborock_b01: unexpected error requesting code")
            return {"base": "unknown"}
        return {}

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Verify the emailed code and create or reauth the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_data = await self._client.code_login_v4(
                    user_input[CONF_ENTRY_CODE]
                )
            except RoborockInvalidCode:
                errors["base"] = "invalid_code"
            except RoborockAccountDoesNotExist:
                errors["base"] = "invalid_email_or_region"
            except RoborockException:
                _LOGGER.exception("roborock_b01: unexpected error verifying code")
                errors["base"] = "unknown_roborock"
            except Exception:
                _LOGGER.exception("roborock_b01: unexpected error verifying code")
                errors["base"] = "unknown"
            else:
                if self.source == config_entries.SOURCE_REAUTH:
                    entry = self._get_reauth_entry()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_USER_DATA: user_data.as_dict(),
                            CONF_BASE_URL: await self._client.base_url,
                        },
                    )
                return self.async_create_entry(
                    title=self._username,
                    data={
                        CONF_USERNAME: self._username,
                        CONF_USER_DATA: user_data.as_dict(),
                        CONF_BASE_URL: await self._client.base_url,
                    },
                )

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_ENTRY_CODE): str}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Restart login for an entry whose credentials expired."""
        self._username = entry_data[CONF_USERNAME]
        self._client = RoborockApiClient(
            username=self._username,
            base_url=entry_data.get(CONF_BASE_URL),
            session=None,
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm reauth dialog: send a fresh code, then ask for it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._request_code()
            if not errors:
                return await self.async_step_code()
        return self.async_show_form(step_id="reauth_confirm", errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """No options flow yet."""
        return _EmptyOptionsFlow()


class _EmptyOptionsFlow(config_entries.OptionsFlow):
    """Placeholder options flow (no options to configure)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """No options to configure."""
        return self.async_create_entry(title="", data={})
