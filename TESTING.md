# Supervised on-device test checklist

Run in order, with the robot on its dock and the Roborock app open on
your phone. **Q7** covers BF/TF/M5/L5 (pv=B01); **Q10** the Q10 series.

## 0. Install

1. Official **Roborock** integration set up and working.
2. Copy `custom_components/roborock_b01/` into `/config/custom_components/`.
3. Restart Home Assistant.
4. Settings -> Devices & Services -> **Add Integration** ->
   **Roborock B01** -> Submit (no login). Or YAML: `roborock_b01:` in
   `configuration.yaml` + restart. Use one method, not both.

## 1. Patches applied

**Do:** Settings -> System -> Logs, search `roborock_b01`.

**Working:** one line like:

```
roborock_b01: patches applied: ['Q7.get_maps', 'Q7.get_segments',
'Q7.clean_segments', 'Q7.position', 'Q7.fan_speed_refresh',
'Q7.CLEAN_AREA', 'Q10.get_maps', 'Q10.CLEAN_AREA']
```

(Only your model's entries matter.)

## 2. Camera first frame

**Do:** open the `camera.<name>_map` entity (or add a Picture Card).

**Working:** Q7 - a rendered PNG within ~2 s. Q10 - the first view may
stay blank until the device pushes a map frame; start a cleaning run or
wait a few seconds.

## 3. Live map updates

**Do:** start a clean from the Roborock app, watch the camera.

**Working:** robot position / cleaned area moves on the map without
reloading anything.

## 4. `roborock.get_maps`

**Do:** Developer Tools -> Actions -> `roborock.get_maps` -> your vacuum
-> Run. Write down the room ids.

**Working:**

```yaml
maps:
  - flag: 7          # Q7: number; Q10: string like "3"
    name: Map 7
    rooms:
      16: Kitchen
      17: Bedroom
```

## 5. Clean one room

**Do:** Developer Tools -> Actions -> `roborock_b01.clean_segment`
(Entity: your vacuum, Segment ids: one small room id from step 4), or
`vacuum.clean_segments` with the same id.

**Working:** after 1-2 s (cloud MQTT latency is normal), the robot
cleans **only that room**. Stop early anytime with `vacuum.stop` or
`vacuum.return_to_base`.

**Failsafe:** before every room clean, the integration fetches the
robot's current room list and refuses wrong ids **before anything is
sent** - the robot can no longer fall back to a full-house clean on
unknown ids (its firmware behavior). The refusal names the bad id and
all valid rooms, e.g. `Room id(s) 99 do not exist on the robot's
current map ... Valid rooms: 16=Kitchen, 17=Bedroom`. If the map can't
be read, the clean is refused too (fail-closed, never gambled).

## 5b. Prove the failsafe (safe to try)

**Do:** run `roborock_b01.clean_segment` with a fake id like `999`.

**Expect:** an instant refusal (nothing reaches the robot - the
protection sensor stays quiet because nothing was blocked by the wire
guard, this is a validation refusal) with the valid room list in the
error. Watch the robot: it must do **nothing**.

## 6. Settings: suction / water / mode / cycles / route

**Do:** Developer Tools -> Actions -> `roborock_b01.clean_settings` ->
your vacuum -> `fan_speed: max` -> Run. Repeat with `water_level: high`,
`clean_mode: vacuum`, `repeat: two`, `clean_route: deep`.

**Working:** each call is accepted after ~1-2 s, the vacuum card's
fan-speed state updates **immediately** (a coordinator refresh runs
right after every successful write - no more waiting for the one-minute
poll), and the Roborock app shows the changed setting. Invalid values
are rejected with a clear message listing the allowed ones. (Q10: not
supported yet - the library has no setters.)

## 7. `roborock.get_vacuum_current_position`

**Do:** with the map warm (step 4 first if needed), run the action.

**Working:** `{"x": <int>, "y": <int>}` roughly matching the robot icon
on the camera map.

## 8. Area mapping survives restarts

**Do:** with a segment-to-area mapping saved (vacuum entity dialog),
check `.storage/roborock_b01_area_mapping` exists; then restart HA.

**Working:** after restart the mapping still works - and if it was
ever lost (Roborock entry re-added), the log shows
`roborock_b01: restoring segment-to-area mapping` (or
`roborock_b01: auto-mapped robot rooms` on a fresh install) and
`vacuum.clean_area` works without redoing the editor.

## 9. Q10 only: goto / zone

**Do (Q10):** `roborock.set_vacuum_goto_position` with x/y from step 6,
then `roborock.set_vacuum_zoned_cleaning` with a small rectangle.

**Q7:** deliberately not available - no verified payload exists; HA
shows the standard "not supported" error.

## 10. Failsafes on the real robot

Work through these in order. After each, check Settings -> System ->
Logs (search `roborock_b01`), Settings -> System -> **Repair**, and
the **Protection** card on the dashboard
(`binary_sensor.<name>_protection` - "on" means something was
blocked or the device is unreachable; the attributes show exactly
what happened and running totals).

### 10a. Blocked command (map protection)

**Do:** Developer Tools -> Actions -> `roborock.send_command` on your
vacuum with command `set_zone_clean` (or any of: `del_map`,
`replace_map`, `set_cur_map`, `rename_map`, `split_room`,
`arrange_room`, `rename_room`, `del_order`, `set_point_clean`).

**Expected:** the action is refused immediately with a `blocked`
error, and the log shows:

```
roborock_b01: command set_zone_clean is blocked: it could delete,
replace or corrupt the saved map / rooms
```

A **Repair** issue appears: *Blocked command: set_zone_clean*.

**Must never happen:** the command reaching the robot. Your map and
rooms are untouched - verify in the Roborock app (map list, room
names, schedules all intact). Clear the Repair issue when done.

### 10b. Transient failure retry

**Do:** hard to trigger on demand - this one is passive. If your
cloud link hiccups you will see:

```
roborock_b01: clean_segments failed (...); retry 1/2
```

and the command still succeeding afterward.

**Must never happen:** a room clean or setting half-applied after a
retry, or endless retries (max 2 retries, then a clean error).

### 10c. Device unreachable (3 failed commands)

**Do:** easiest safe trigger: unplug the robot (or put it in a
hard-offline state), then run `roborock_b01.clean_segment` three
times.

**Expected:** each call fails after its retries (a few seconds each),
and after the third the log plus a **Repair** issue:

*Roborock device unreachable (3 failed commands)* - red, with a
count that rises if you keep trying.

**Must never happen:** HA hanging or restarting; the issue failing
to clear. Plug the robot back in, run one successful command (e.g.
`roborock.get_maps`) - the issue disappears by itself.

### 10d. Guard never blocks normal traffic

**Do:** run the normal flow once more end to end: camera first frame,
`roborock.get_maps`, `roborock_b01.clean_segment` on one small room,
one `clean_settings` change.

**Expected:** everything works exactly as in steps 2-6, with **no**
blocked errors and no Repair issues.

**Must never happen:** a blocked error for `set_room_clean`,
`set_prop`, `get_map_list`, `upload_by_mapid`, or any dashboard
button.

### Paste-ready bug report

If anything misbehaves: Settings -> System -> Logs, set `roborock_b01`
to debug (three-dot menu -> reuse nothing else), reproduce, and paste
the `roborock_b01` lines plus the Repair issue text into a new issue.

**Do (Q10):** `roborock.set_vacuum_goto_position` with x/y from step 6,
then `roborock.set_vacuum_zoned_cleaning` with a small rectangle.

**Q7:** deliberately not available - no verified payload exists; HA
shows the standard "not supported" error.
