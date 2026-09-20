"""Constants for the Roborock B01 (Q7/Q10) full-support integration."""

DOMAIN = "roborock_b01"
PLATFORMS = ["camera"]

# Delay before a one-shot re-scan for coordinators that appeared after our
# setup ran (the core roborock entry may still be initializing).
LATE_SCAN_DELAY = 20  # seconds
