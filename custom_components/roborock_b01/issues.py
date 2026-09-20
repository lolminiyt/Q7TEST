"""Repair issues + protection status for refusals and device failures.

One event stream (``failsafe.add_event_listener``) feeds two UI
surfaces:

- **Repair issues**: ``blocked_command`` (WARNING) when something tries
  a map-destroying command; ``device_failures`` (ERROR) after three
  consecutive failed device round-trips. The failure issue clears
  itself on the next successful command.
- **The protection status holder**: counters and last-event fields the
  ``binary_sensor.roborock_b01_protection`` entity renders, so the
  dashboard can show reachability and the last blocked command.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback

from .const import (
    ATTR_BLOCKED_COUNT,
    ATTR_FAILURE_COUNT,
    ATTR_LAST_BLOCKED,
    ATTR_LAST_FAILURE,
    ATTR_LAST_SUCCESS,
    ATTR_UNREACHABLE,
    DOMAIN,
    UNREACHABLE_THRESHOLD,
)
from .failsafe import add_event_listener

_LOGGER = logging.getLogger(__name__)

BLOCKED_ISSUE_ID = "blocked_command"
FAILURES_ISSUE_ID = "device_failures"
_CONSECUTIVE_FAILURES = UNREACHABLE_THRESHOLD


class ProtectionStatus:
    """Process-wide failsafe event state for the UI surfaces.

    Lives as long as the patches do (subscribed once in setup), so it
    keeps counting across entry reloads. Entities register to be
    refreshed on every event and unregister on removal.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self.last_blocked: str | None = None
        self.blocked_count = 0
        self.last_failure: str | None = None
        self.failure_count = 0
        self.last_success: str | None = None
        self.fail_streak = 0
        self._entities: list = []

    # ----------------------------------------------------- entity glue

    def register(self, entity) -> None:
        if entity not in self._entities:
            self._entities.append(entity)

    def unregister(self, entity) -> None:
        if entity in self._entities:
            self._entities.remove(entity)

    def _notify_entities(self) -> None:
        for entity in list(self._entities):
            try:
                entity.async_write_ha_state()
            except Exception:
                _LOGGER.exception("roborock_b01: protection entity refresh failed")

    # --------------------------------------------------- event handler

    @callback
    def handle_event(self, event: str, command: str, err: str) -> None:
        """Update counters/issues and refresh registered entities."""
        from homeassistant.helpers import issue_registry as ir

        if event == "blocked":
            self.last_blocked = command
            self.blocked_count += 1
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                BLOCKED_ISSUE_ID,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="blocked_command",
                translation_placeholders={"command": command},
                data={"command": command, "count": self.blocked_count},
            )
        elif event == "device_failure":
            self.last_failure = f"{command}: {err}"
            self.failure_count += 1
            self.fail_streak += 1
            if self.fail_streak == _CONSECUTIVE_FAILURES:
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    FAILURES_ISSUE_ID,
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="device_failures",
                    translation_placeholders={"failures": str(self.fail_streak)},
                    data={"failures": self.fail_streak},
                )
            elif self.fail_streak > _CONSECUTIVE_FAILURES:
                ir.async_update_issue(
                    self._hass,
                    DOMAIN,
                    FAILURES_ISSUE_ID,
                    data={"failures": self.fail_streak},
                )
        elif event == "device_success":
            self.last_success = command
            self.fail_streak = 0
            ir.async_delete_issue(self._hass, DOMAIN, FAILURES_ISSUE_ID)
        self._notify_entities()

    # ------------------------------------------------ entity attributes

    def attributes(self) -> dict:
        return {
            ATTR_LAST_BLOCKED: self.last_blocked,
            ATTR_BLOCKED_COUNT: self.blocked_count,
            ATTR_LAST_FAILURE: self.last_failure,
            ATTR_FAILURE_COUNT: self.failure_count,
            ATTR_LAST_SUCCESS: self.last_success,
            ATTR_UNREACHABLE: self.fail_streak >= _CONSECUTIVE_FAILURES,
        }


def get_protection_status(hass: HomeAssistant) -> ProtectionStatus | None:
    """The process-wide holder, if setup ran."""
    return hass.data.get(DOMAIN, {}).get("protection")


@callback
def async_setup_issue_reporter(hass: HomeAssistant) -> callable:
    """Subscribe the holder to failsafe events (once per process).

    Returns the unsubscribe function; kept for the process lifetime to
    match the patch lifetime (issues survive entry reloads).
    """
    holder = ProtectionStatus(hass)
    hass.data.setdefault(DOMAIN, {})["protection"] = holder
    return add_event_listener(holder.handle_event)
