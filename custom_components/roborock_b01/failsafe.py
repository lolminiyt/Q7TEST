"""Failsafe layer shared by all command paths.

Two guarantees, enforced in one place so every path gets them:

1. **Bounded retry** - every device round-trip is retried on transient
   ``RoborockException`` (the B01 protocol is MQTT/cloud; single
   timeouts happen). Retries are capped and backed off, so a genuinely
   offline robot still fails fast with a translated error instead of
   hanging or half-applying settings.

2. **Destructive-command wire guard** - the Q7 RPC channel refuses to
   send any command that can destroy or corrupt the saved map (delete,
   replace or switch maps, split/arrange/rename rooms) or the
   unverified point/zone payloads. Blocking at the channel means
   nothing going through Home Assistant - any integration, script or
   UI - can wipe the map, even if some other code path grows a bug.
   The Roborock app is unaffected (it does not go through HA).
"""

from __future__ import annotations

import asyncio
import logging

from roborock.exceptions import RoborockException

_LOGGER = logging.getLogger(__name__)

# Delay between retry attempts; the last attempt happens after the last
# delay. Kept module-level so tests can shrink it.
RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0)

# Q7 commands this integration refuses to put on the wire. Each one can
# destroy or distort the saved map / room structure / schedules, or is
# an unverified payload shape (point/zone cleaning on Q7).
Q7_BLOCKED_COMMANDS = frozenset(
    {
        # Map destruction / replacement / switching
        "DEL_MAP",
        "REPLACE_MAP",
        "SET_CUR_MAP",
        "RENAME_MAP",
        # Room structure changes (would invalidate the area mapping)
        "SPLIT_ROOM",
        "ARRANGE_ROOM",
        "RENAME_ROOM",
        "RENAME_ROOMS",
        # Schedule deletion
        "DEL_ORDER",
        "DEL_ORDERS",
        # Unverified payload shapes (no-guesses rule, enforced twice)
        "SET_POINT_CLEAN",
        "SET_ZONE_CLEAN",
        "SET_ZONE_POINTS",
        "START_POINT_CLEAN",
    }
)


class BlockedCommandError(Exception):
    """A command was refused by the local wire guard (never sent)."""


# ----------------------------------------------------------------- events
# The channel guard runs without any ``hass`` reference (it wraps the
# library's channel class), so instead of calling the issue registry
# directly it emits events here; the component subscribes in setup and
# raises repair issues / logs. Test code can also listen here.
_listeners: list = []


def add_event_listener(callback) -> callable:
    """Subscribe to failsafe events; returns the remove function."""
    _listeners.append(callback)

    def _remove() -> None:
        if callback in _listeners:
            _listeners.remove(callback)

    return _remove


def _emit(event: str, command: str, err: str) -> None:
    for cb in _listeners:
        try:
            cb(event, command, err)
        except Exception:
            _LOGGER.exception("roborock_b01: failsafe listener error")


def assert_q7_command_allowed(command) -> None:
    """Raise BlockedCommandError if ``command`` may destroy map data.

    Commands arrive as ``RoborockB01Q7Methods`` enum members whose
    ``str()`` is the wire value (``service.del_map``), not the member
    name. Normalize both spellings: bare names (``DEL_MAP``) and wire
    values (``service.del_map``) must hit the blocklist.
    """
    name = getattr(command, "name", None) or str(command)
    if name.upper() in Q7_BLOCKED_COMMANDS:
        _emit(
            "blocked",
            str(command),
            "command is on the map-protection blocklist",
        )
        raise BlockedCommandError(
            f"roborock_b01: command {command} is blocked: it could "
            "delete, replace or corrupt the saved map / rooms"
        )


async def async_with_retry(desc: str, func, *args, **kwargs):
    """Run ``func`` with bounded retries on transient failures.

    Non-Roborock exceptions (programming errors, validation) are never
    retried. After the final attempt the original exception propagates.
    """
    last: Exception | None = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            result = await func(*args, **kwargs)
            # Signal success on every completed command so consumers
            # can clear "device unreachable" state.
            _emit("device_success", desc, "recovered" if attempt else "ok")
            return result
        except RoborockException as err:
            last = err
            _emit("device_failure", desc, str(err))
            if attempt < len(RETRY_DELAYS):
                _LOGGER.warning(
                    "roborock_b01: %s failed (%s); retry %d/%d",
                    desc,
                    err,
                    attempt + 1,
                    len(RETRY_DELAYS),
                )
                await asyncio.sleep(RETRY_DELAYS[attempt])
    assert last is not None
    raise last
