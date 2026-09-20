"""Constants for the Roborock B01 (Q7/Q10) full-support integration."""

DOMAIN = "roborock_b01"
PLATFORMS = ["camera", "binary_sensor"]

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
# setup ran (the core roborock entry may still be initializing).
LATE_SCAN_DELAY = 20  # seconds
