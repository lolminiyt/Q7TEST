# Roborock B01 (Q7 / Q10) Full Support for Home Assistant

Standalone custom integration for B01-protocol vacuums (`pv=B01`:
Q7 BF/TF/M5/L5, Q10 series). It logs into your Roborock account itself,
connects your devices via the python-roborock library, and provides
**its own vacuum entities with all controls** - the official Roborock
integration is NOT needed and does NOT have to be installed. Every
command and data path is taken from the python-roborock library source
and its device-verified test suite - nothing is guessed.

## What you get

Per Q7 / Q10 device (owned entities, no duplicates):

- `vacuum.<name>` with **full controls**:
  - Q7: start / pause / stop / return to dock, fan speed (quiet,
    balanced, turbo, max, max_plus), locate, room cleaning
    (`service.set_room_clean` with the library's verified payload),
    `get_maps`, `get_vacuum_current_position`, and the
    `clean_settings` extras below.
  - Q10: start / pause / stop / return to dock / spot clean, fan
    level, locate, room cleaning, goto position, zone cleaning.
- `camera.<name>_map` - **live map PNG, push-driven**. The device
  streams map frames over MQTT while cleaning; the integration only
  listens. No polling, no extra connection.
- `roborock_b01.clean_settings` service (Q7) - set **suction power**
  (quiet, balanced, turbo, max, max_plus), **water flow** (low,
  medium, high), **clean mode** (vacuum / vac_and_mop / mop),
  **repeat cycles** (one, two) and **cleaning route** (balanced,
  deep). Every write is instantly reflected (post-write refresh).
- `roborock_b01.clean_segment` service - clean rooms by numeric id,
  with ids verified against the robot's current map before sending.
- `binary_sensor.<name>_protection` - "on" when a command was blocked
  or the device is unreachable; attributes show the last blocked
  command, failure details and running totals.

Entity behavior is a copy of HA core's Roborock vacuum classes
(Apache-2.0, attribution in `b01_vacuum.py`), extended with the map and
room features the official integration lacks on these models.

## Failsafes

- **Bounded retries** - device commands and map reads are retried
  (2 retries with backoff) on transient failures; persistent failure
  fails fast with a clear translated error. Commands are never
  half-applied.
- **Map-protection wire guard** - the integration refuses to put any
  map-destroying command on the wire (`DEL_MAP`, `REPLACE_MAP`,
  `SET_CUR_MAP`, `RENAME_MAP`, room structure changes like
  `SPLIT_ROOM`/`ARRANGE_ROOM`/`RENAME_ROOM(S)`, schedule deletion, and
  unverified Q7 point/zone payloads). The guard wraps the library's
  channel class, so every send path is covered. The Roborock app is
  not affected.
- **Expired credentials heal through reauth** - no password is
  stored: the config flow signs in once with an emailed verification
  code and keeps the returned session blob. When MQTT later rejects
  it, the integration starts HA's reauth flow (new code -> updated
  entry -> reload) instead of failing silently.
- **Mapping keeper is read-mostly and self-healing** - see below.

## "Area mapping is not configured"

The built-in `vacuum.clean_area` action needs a one-time
**segment-to-area mapping** saved in the entity registry. Open the
vacuum entity's settings dialog and save the mapping once. The keeper:

- backs the mapping up to `.storage/roborock_b01_area_mapping` and
  **restores it automatically** if the registry entry is ever rebuilt -
  but only against the robot's current room list, never blind
  (fail-closed);
- **auto-maps rooms to HA areas by name** when no mapping exists
  ("Kitchen" -> "Kitchen" area, longest match wins, unmatched rooms
  are left out rather than guessed);
- trims stale room ids after a map recovery, rebuilds the mapping,
  or raises the *Room mapping needs redoing* repair issue.

Meanwhile, `roborock_b01.clean_segment` cleans rooms by id with no
mapping at all; unknown or stale ids are refused with the valid-room
list instead of triggering the device's full-house fallback (the Q7
firmware silently cleans everything when `SET_ROOM_CLEAN` carries
unknown room ids).

## Install

1. Copy `custom_components/roborock_b01/` to
   `/config/custom_components/` (or add as a HACS custom repository).
   Requires **HA 2026.9+** and pins `python-roborock==7.8.1` in
   `manifest.json` (newer than the official integration's 7.1.1 -
   needed for the B01 map parser's room extraction).
2. Restart HA.
3. Settings -> Devices & Services -> **Add Integration** ->
   **Roborock B01** -> enter your **Roborock account email** (the same
   you use in the Roborock app), submit, then enter the emailed
   **verification code**.
4. The log should show `roborock_b01: Q7.wire_guard installed` and
   `coordinator ready for <name> (<model>)`.
5. Check for `vacuum.<your_robot>`, `camera.<your_robot>_map` and
   `binary_sensor.<your_robot>_protection`, then try a room clean.

UI setup only - no YAML configuration.

## Test order

1. Open the map camera, start a clean, watch it update live (pushes).
2. Toggle fan speed on the vacuum card - the state reflects instantly.
3. `roborock_b01.clean_segment` (or `vacuum.clean_segments`) with one
   room id from the map camera's room list.
4. `roborock_b01.clean_settings` - change suction power / water level.
5. On a Q10: goto / zone controls. On a Q7: not available (see limits).

## Honest limits

- **Q7 goto/zone is not implemented.** No verified wire payload exists
  for Q7 point/zone cleaning in the library or community captures;
  the guard blocks the unverified shapes rather than guessing.
- B01 is **MQTT-only by design** (no local TCP for this protocol) -
  expect ~1-2 s cloud latency on commands. That is the protocol.
- `get_maps` returns the **current** map only.
- Empty room lists mean the map has unnamed rooms - name them in the
  Roborock app and retry.

## Files

- `manifest.json` - standalone hub integration, no dependencies
- `__init__.py` - wire guard + issue reporter + hub setup + services
- `session.py` - stored-credential session + DeviceManager (reauth wiring)
- `hub.py` - coordinator registry (`hass.data[DOMAIN]["coordinators"]`)
- `coordinators.py` - Q7 poll / Q10 push-driven coordinators
- `b01_vacuum.py` - owned Q7/Q10 vacuum entities (core copy, Apache-2.0)
- `vacuum.py` - wire guard + room helpers (`install_q7_wire_guard`)
- `camera.py` - push-driven map cameras
- `binary_sensor.py` - protection sensor
- `area_mapping.py` - backup / restore / auto-map / stale-trim keeper
- `services.py` - `clean_segment` + `clean_settings`
- `config_flow.py` - email-code login flow
- `strings.json` / `translations/en.json` - UI text
