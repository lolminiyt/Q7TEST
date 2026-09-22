"""Constants for the Roborock B01 (Q7/Q10) full-support integration."""

from __future__ import annotations

DOMAIN = "roborock_b01"

# "vacuum" first: the map camera renders the vacuum's room ids, and the
# area-mapping keeper watches vacuum entities, so the vacuum platform
# must be set up before "camera" / "binary_sensor".
PLATFORMS = ["vacuum", "camera", "binary_sensor"]

# Config-entry data keys. CONF_USERNAME comes from homeassistant.const;
# the rest are ours. No password is stored: the config flow signs in
# once with the emailed verification code and keeps the returned
# user_data blob.
CONF_USER_DATA = "user_data"
CONF_BASE_URL = "base_url"
CONF_ENTRY_CODE = "code"

# How often the Q7 coordinator polls device properties (seconds). The Q10
# is push-driven and needs no poll.
Q7_POLL_INTERVAL = 60

# Diagnostics entity names (one per integration, not per device).
PROTECTION_ENTITY = "roborock_b01_protection"

# Attributes of the protection diagnostics entity.
ATTR_LAST_BLOCKED = "last_blocked_command"
ATTR_BLOCKED_COUNT = "blocked_commands_total"
ATTR_LAST_FAILURE = "last_device_failure"
ATTR_FAILURE_COUNT = "failed_commands_total"
ATTR_LAST_SUCCESS = "last_device_success"
ATTR_UNREACHABLE = "device_unreachable"

# The protection entity flips to "problem" after this many consecutive
# failures (matches the Repair issue threshold).
UNREACHABLE_THRESHOLD = 3

# Delay before a one-shot re-scan for coordinators that appeared after our
# setup ran (devices may still be connecting).
LATE_SCAN_DELAY = 20  # seconds
