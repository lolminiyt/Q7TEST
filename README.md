# Roborock B01 (Q7 / Q10) Full Support for Home Assistant

HACS-installable custom integration that completes Home Assistant core
support for B01-protocol vacuums (`pv=B01`: Q7 BF/TF/M5/L5, Q10 series),
built from [python-roborock `docs/DEVICES.md`](https://github.com/Python-roborock/python-roborock/blob/main/docs/DEVICES.md).

**No extra login.** It reuses the authenticated session of the official
Roborock integration. Set up the official integration first.

## Install via HACS

1. HACS → Integrations → ⋮ menu → **Custom repositories**
2. Paste this repo URL, category **Integration**, Add
3. Find **Roborock B01 (Q7/Q10) Full Support** in HACS → Install
4. Restart Home Assistant
5. Settings → Devices & Services → **Add Integration** → **Roborock B01**
   → Submit (no login — it reuses the official Roborock integration,
   which must already be set up)

YAML alternative (no UI entry): add `roborock_b01:` to
`configuration.yaml` and restart. Use one method, not both.

## What you get (Q7 BF and friends)

- `roborock.get_maps` — current map id + room ids/names
- Room segments (`CLEAN_AREA`, room list UI) + room cleaning
- `roborock.get_vacuum_current_position`
- `roborock.set_vacuum_goto_position` — EXPERIMENTAL
- `roborock.set_vacuum_zoned_cleaning` — EXPERIMENTAL
- New `camera.<device>_map` — live map PNG, push-driven while cleaning
- Q10 also gains `roborock.get_maps`

Full details, B01 protocol notes and limits:
[`custom_components/roborock_b01/README.md`](custom_components/roborock_b01/README.md).
