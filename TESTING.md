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
'Q7.clean_segments', 'Q7.position', 'Q7.CLEAN_AREA', 'Q10.get_maps',
'Q10.CLEAN_AREA']
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

## 6. `roborock.get_vacuum_current_position`

**Do:** with the map warm (step 4 first if needed), run the action.

**Working:** `{"x": <int>, "y": <int>}` roughly matching the robot icon
on the camera map.

## 7. Q10 only: goto / zone

**Do (Q10):** `roborock.set_vacuum_goto_position` with x/y from step 6,
then `roborock.set_vacuum_zoned_cleaning` with a small rectangle.

**Q7:** deliberately not available - no verified payload exists; HA
shows the standard "not supported" error.
