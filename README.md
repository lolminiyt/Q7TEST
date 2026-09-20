# Roborock B01 (Q7/Q10) Full Support for Home Assistant

HACS-installable custom integration that completes Home Assistant core
support for B01-protocol vacuums (`pv=B01`: Q7 BF/TF/M5/L5, Q10 series).
All commands are taken from the python-roborock library and its
device-verified test suite - nothing guessed.

**No extra login.** It reuses the authenticated session of the official
Roborock integration. Set up the official integration first.

## Install via HACS

1. HACS -> Integrations -> ⋮ menu -> **Custom repositories**
2. Paste this repo URL, category **Integration**, Add
3. Find **Roborock B01 (Q7/Q10) Full Support** -> Install
4. Restart Home Assistant
5. Settings -> Devices & Services -> **Add Integration** ->
   **Roborock B01** -> Submit (no login)

YAML alternative (no UI entry): add `roborock_b01:` to
`configuration.yaml` and restart. Use one method, not both.

## What you get (Q7 BF / Q10 and friends)

- `camera.<name>_map` - **live map PNG, push-driven while cleaning**
- **Room cleaning** (`vacuum.clean_segments` + a `clean_segment`
  service) - verified Q7 `set_room_clean` / Q10 dpStartClean payloads
- `roborock.get_maps` - map id + room ids/names (Q7 + Q10)
- `roborock.get_vacuum_current_position` (Q7 + Q10)
- `roborock.set_vacuum_goto_position` - Q10 (core-provided, verified on
  hardware by the library authors). Not available on the Q7 - no
  verified payload exists, so none is sent.
- `roborock.set_vacuum_zoned_cleaning` - Q10 only, same as above.

Full details, protocol notes and limits:
[`custom_components/roborock_b01/README.md`](custom_components/roborock_b01/README.md).
