# Roborock B01 (Q7 / Q10) Full Support for Home Assistant

Custom integration that completes Home Assistant core support for
B01-protocol vacuums (`pv=B01`: Q7 BF/TF/M5/L5, Q10 series). Every
command and data path used here is taken from the python-roborock
library source and its own device-verified test suite - nothing is
guessed.

**No extra login.** It reuses the authenticated session of the official
Roborock integration (its coordinators expose the library APIs). Set up
the official integration first.

## What you get

On every B01 Q7 / Q10 vacuum (entities attach to the existing core
device - no duplicates):

- `camera.<name>_map` - **live map PNG, push-driven**. The device itself
  streams map frames over MQTT while cleaning; the integration only
  listens. No polling, no heartbeat, no extra MQTT connection.
- **Room cleaning** - `vacuum.clean_segments` with numeric room ids.
  On the Q7 this is implemented with the library's verified
  `service.set_room_clean` wrapper (`clean_type=1, ctrl_value=1,
  room_ids=[...]`).
- `roborock.get_maps` - current map id + room ids/names.
- `roborock.get_vacuum_current_position` - robot x/y from the parsed
  live map.
- `roborock.set_vacuum_goto_position` / `set_vacuum_zoned_cleaning`:
  **Q10 only.** Already implemented by HA core on top of the library's
  hardware-verified `vacuum.goto_position` / `vacuum.clean_zone`
  wrappers - this package deliberately does not touch them. On the Q7
  these services are **not available**: the library has no wrapper for
  Q7 point/zone cleaning and no verified payload shape exists (checked
  upstream and community sources).
- `roborock_b01.clean_segment` service (works even where the core
  segment-repair UI flow is unavailable).

## Install

Via UI (preferred):

1. Copy `custom_components/roborock_b01/` to `/config/custom_components/`
   (or install via HACS custom repository).
2. Restart HA.
3. Settings -> Devices & Services -> **Add Integration** ->
   **Roborock B01** -> Submit. No credentials - it reuses the official
   Roborock session.
4. The log should show: `roborock_b01: patches applied: [...]`.
5. Check for `camera.<your_vacuum>_map` and try room cleaning.

YAML alternative: `roborock_b01:` in `configuration.yaml` + restart.
Use one method, not both.

## Test order

1. Open the map camera, start a clean, watch it update live (pushes).
2. Developer Tools -> Actions -> `roborock.get_maps` -> note room ids.
3. `roborock_b01.clean_segment` (or `vacuum.clean_segments`) with one
   room id.
4. `roborock.get_vacuum_current_position`.
5. On a Q10: goto/zone (core-provided). On a Q7: not available - see
   limits below.

## Honest limits

- **Q7 goto/zone is not implemented.** No library wrapper exists and no
  verified wire payload could be found (upstream python-roborock main
  still lacks one; no community MQTT capture documents the shapes). If
  you capture the payload the Roborock app sends for point/zone cleaning
  on a Q7, please open an issue - it can then be implemented with
  confidence.
- B01 is **MQTT-only by design** (no local TCP for this protocol) -
  expect ~1-2 s cloud latency on commands. That is the protocol, not a
  bug in this package.
- `get_maps` returns the **current** map only.
- Empty room lists mean the map has unnamed rooms - name them in the
  Roborock app and re-run `roborock.get_maps`.

## Files

- `manifest.json` - `dependencies: ["roborock"]`, no login
- `__init__.py` - applies patches once, registers services, loads camera
- `vacuum.py` - all Q7/Q10 control patches (`patch_b01_vacuum_classes`)
- `camera.py` - push-driven map cameras (library listener pattern)
