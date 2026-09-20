# Roborock B01 (Q7 / Q10) Full Support for Home Assistant

Custom integration that completes Home Assistant core support for B01-protocol
vacuums (`pv=B01`: Q7 BF/TF/M5/L5, Q10 series), built from
[python-roborock `docs/DEVICES.md`](https://github.com/Python-roborock/python-roborock/blob/main/docs/DEVICES.md).

**No extra login.** It reuses the authenticated session of the official
Roborock integration (same `DeviceManager` / coordinators). Set up the
official integration first.

## Why B01 needs this

Per `DEVICES.md`, B01 differs from V1:

| | V1 (S7/S8/…) | B01 (Q7/Q10) |
|---|---|---|
| Transport | MQTT + local TCP `:58867` | **MQTT only** |
| Protocol | JSON RPC + AES | DPS protocol |
| Channel | `V1Channel` + `RpcChannel` | `MqttChannel` + helpers |
| Maps | `home` / `maps` traits | Q7: `map` + `map_content`; Q10: `maps` + `map` |
| Live map | poll `map_content.refresh()` | Q7 **streams frames while cleaning** via `map_content.add_update_listener` |

HA core implements the Q7/Q10 basics (start/pause/stop/dock/locate/fan,
sensors) but leaves `get_maps` as `ServiceNotSupported` for both, gives Q7
no segments/position/goto/zone, and creates no map camera. This package
fills exactly those gaps using APIs the library already exposes.

## What you get

On `vacuum.roborock_q7_bf` (and all B01 Q7):

- `roborock.get_maps` — current map id + room ids/names from
  `GET_MAP_LIST` + `UPLOAD_BY_MAPID` (`MapData.rooms`)
- Room segments (`CLEAN_AREA` flag, room list UI) + room cleaning via
  `SET_ROOM_CLEAN`
- `roborock.get_vacuum_current_position` from `map_data.vacuum_position`
- `roborock.set_vacuum_goto_position` — EXPERIMENTAL via
  `service.set_point_clean` (undocumented shape, device-validated)
- `roborock.set_vacuum_zoned_cleaning` — EXPERIMENTAL via
  `service.set_zone_clean`
- New `camera.<device>_map` — live map PNG, push-driven while cleaning,
  attached to the existing Roborock device (no polling loop)

On Q10: `roborock.get_maps` (rooms from `api.map.rooms`, flag from
`maps.current_map_id`). Position/goto/zone/segments already exist in core.

## Install

1. Copy `custom_components/roborock_b01/` to `/config/custom_components/`.
2. If you installed the earlier `q7bf_map_patch`, **remove it** (this
   supersedes it) and drop `q7bf_map_patch:` from `configuration.yaml`.
3. Add to `/config/configuration.yaml`:
   ```yaml
   roborock_b01:
   ```
4. Restart HA. Log should show:
   `roborock_b01: vacuum patches applied: [...]`
5. Check for `camera.roborock_q7_bf_map`.

## Test order

1. Developer Tools → Actions → `roborock.get_maps` on the Q7 vacuum.
2. `roborock.get_vacuum_current_position`.
3. Room list / room clean with one room.
4. Open the map camera; run a clean and watch it update live.
5. Goto/zone last, supervised — they log `EXPERIMENTAL` lines with params.

## Limits (honest)

- `get_maps` returns the **current** map only. `MapContentTrait` fetches
  `current_map_id`; multi-floor enumeration needs a library extension.
- Goto/zone param shapes are best-effort (no wrapper in python-roborock).
  The device rejects bad shapes with an error instead of moving; paste the
  log lines back to lock them in.
- Expect MQTT latency (no local TCP exists for B01 — by design, see
  `DEVICES.md`, not a bug in this package).
- Empty `rooms: {}` means the map has no named rooms/outlines — name rooms
  in the Roborock app and re-run.

## Files

- `manifest.json` — `dependencies: ["roborock"]`, no login, no config flow
- `__init__.py` — applies vacuum patches, loads camera platform
- `vacuum.py` — all B01 patches in one place (`patch_b01_vacuum_classes`)
- `camera.py` — push-driven map cameras (the `DEVICES.md` listener pattern)
