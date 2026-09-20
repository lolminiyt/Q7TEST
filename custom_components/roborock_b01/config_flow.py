"""Config flow for Roborock B01 full support (UI setup).

No credentials needed: this integration only unlocks map/room/position
features on top of the official Roborock integration, reusing its session.
Single instance is enough, so the flow is one confirm step.
"""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from . import DOMAIN


class RoborockB01ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle UI setup for Roborock B01."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm single-instance setup."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Roborock B01", data={})
        return self.async_show_form(step_id="user")
