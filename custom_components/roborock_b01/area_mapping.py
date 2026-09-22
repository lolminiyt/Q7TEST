"""Keep the vacuum's segment-to-area mapping alive (and repair it).

Core stores the mapping in the vacuum entity's **entity registry**
options under ``options["vacuum"]``:

- ``area_mapping``: ``{area_id: [segment_id, ...]}`` (segment ids as
  strings; verified against homeassistant/components/vacuum/__init__.py)
- ``last_seen_segments``: ``[{"id": ..., "name": ...}, ...]`` - the
  segment list as last saved by the mapping editor.

Nothing in this integration deletes that data, but it dies with the
entity registry entry itself: removing/re-adding the Roborock entry or
restoring a registry backup made before the mapping was saved loses
it. This module makes that survivable:

1. **Backup** - whenever the registry carries a mapping, it is copied
   to ``.storage/roborock_b01_area_mapping``.
2. **Restore** - after a restart or registry loss, if the vacuum
   entity was rebuilt (no options) but the robot still reports the
   same rooms, the backup is written back verbatim.
3. **Auto-map** - with no backup either (first run, or the robot's
   rooms changed), rooms are mapped onto HA areas by name: a room
   named "Kitchen" lands in the "Kitchen" area. Name match is exact or
   one containing the other (case-insensitive); the longest match
   wins. Rooms with no matching area are left unmapped rather than
   guessed.

Reconcile runs when a coordinator appears (startup, late load, or a
rebuilt core entry) and once more after a short settle delay - every
loss path ends in a coordinator (re)appearing, so no registry event
listener is needed. The backup is single-slot: one robot per HA
instance (matches this integration's single-instance config flow).
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "roborock_b01_area_mapping"
STORAGE_VERSION = 1
# Time for our entities to register (and their registry entries to
# settle) after the hub creates the coordinators.
_RECONCILE_DELAY = 10  # seconds


@callback
def _async_entity_registry(hass: HomeAssistant):
    from homeassistant.helpers import entity_registry

    return entity_registry.async_get(hass)


@callback
def _async_device_registry(hass: HomeAssistant):
    from homeassistant.helpers import device_registry

    return device_registry.async_get(hass)


@callback
def _async_area_registry(hass: HomeAssistant):
    from homeassistant.helpers import area_registry

    return area_registry.async_get(hass)


def _vacuum_options(registry_entry) -> dict:
    return registry_entry.options.get("vacuum", {}) if registry_entry else {}


def _backup_payload(area_mapping: dict, last_seen) -> dict:
    """Options shape mirroring what core's mapping editor saves."""
    return {
        "area_mapping": area_mapping,
        "last_seen_segments": last_seen
        or [
            {"id": seg, "name": f"Segment {seg}"}
            for segs in area_mapping.values()
            for seg in segs
        ],
    }


class _Keeper:
    """Backup / restore / auto-map for the segment-to-area mapping."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    # ---------------------------------------------------- validation

    @staticmethod
    def _valid_mapping(mapping) -> bool:
        """True if ``mapping`` matches core's expected shape exactly.

        Never write anything derived from a payload that does not
        validate - a corrupt restore must be impossible.
        """
        return (
            isinstance(mapping, dict)
            and bool(mapping)
            and all(
                isinstance(area, str)
                and area
                and isinstance(segs, list)
                and bool(segs)
                and all(isinstance(seg, str) and seg for seg in segs)
                for area, segs in mapping.items()
            )
        )

    # -------------------------------------------------- registry lookup

    def _vacuum_registry_entry(self, coordinator):
        """Registry entry of this coordinator's vacuum entity."""
        dr = _async_device_registry(self.hass)
        device = dr.async_get_device(identifiers={("roborock", coordinator.duid)})
        if device is None:
            return None
        er = _async_entity_registry(self.hass)
        for entry in er.entities.values():
            if (
                getattr(entry, "platform", None) == DOMAIN
                and getattr(entry, "device_id", None) == device.id
                and str(getattr(entry, "entity_id", "")).startswith("vacuum.")
            ):
                return entry
        return None

    def _vacuum_entity(self, entity_id: str):
        """The vacuum entity object (None until the platform wires it)."""
        from homeassistant.helpers.entity_component import DATA_INSTANCES

        component = self.hass.data.get(DATA_INSTANCES, {}).get("vacuum")
        getter = getattr(component, "get_entity", None)
        return getter(entity_id) if getter else None

    def _vacuum_entity_for_coordinator(self, coordinator):
        registry_entry = self._vacuum_registry_entry(coordinator)
        if registry_entry is None:
            return None
        return self._vacuum_entity(registry_entry.entity_id)

    # ------------------------------------------------------- reconcile

    async def async_reconcile(self, coordinator) -> None:
        """Backup the live mapping, or rebuild one from backup/auto-map.

        Invariants (failsafe):
        - a healthy registry mapping is only copied to backup, never
          rewritten - unless it contains segment ids the robot no longer
          reports, in which case exactly those ids are trimmed away;
        - a restore only happens with the robot's live room list in
          hand: the backup's ids are filtered against it, so stale ids
          (e.g. after a map recovery) can never be written unchecked;
        - nothing invalid ever reaches the registry or the backup.

        Known limitation, deliberately accepted: trimming compares ids
        only. If a map recovery renumbers rooms, an old id that now
        names a different room survives the trim - but the room-level
        guard in ``vacuum.py`` still refuses any id the robot does not
        know, so a stale mapping can never trigger a full-house clean.
        """
        registry_entry = self._vacuum_registry_entry(coordinator)
        if registry_entry is None:
            return  # entity not created yet; the delayed pass re-checks
        er = _async_entity_registry(self.hass)
        options = _vacuum_options(registry_entry)
        area_mapping = options.get("area_mapping")
        live: set[str] | None = None
        if area_mapping:
            live = await self._async_live_segments(coordinator)

        if (
            area_mapping
            and live is not None
            and not self._mapping_is_current(area_mapping, live)
        ):
            trimmed = self._trim_stale_segments(area_mapping, live)
            if trimmed is not None:
                payload = _backup_payload(trimmed, options.get("last_seen_segments"))
                er.async_update_entity_options(
                    registry_entry.entity_id, "vacuum", payload
                )
                _LOGGER.info(
                    "roborock_b01: removed stale segment ids from the "
                    "segment-to-area mapping of %s (robot no longer "
                    "reports them): %s",
                    registry_entry.entity_id,
                    trimmed,
                )
                await self._store.async_save(payload)
                # The trimmed mapping only references rooms the robot
                # still reports: any earlier staleness issue is resolved.
                self._clear_stale_issue(registry_entry)
                return
            # Every mapped id is stale: drop the unusable mapping and
            # rebuild below instead of leaving it in place.
            _LOGGER.info(
                "roborock_b01: the segment-to-area mapping of %s no "
                "longer matches any room the robot reports; rebuilding",
                registry_entry.entity_id,
            )
            er.async_update_entity_options(registry_entry.entity_id, "vacuum", None)
        elif area_mapping:
            # Healthy (or the robot is unreadable - fail-closed: never
            # touch data we cannot verify): keep the backup current,
            # nothing else.
            await self._store.async_save(
                _backup_payload(area_mapping, options.get("last_seen_segments"))
            )
            if live is not None:
                self._clear_stale_issue(registry_entry)
            return

        restored: dict | None = None

        # 1) Best-effort restore from the validated backup. Fail-closed:
        # only ids the robot currently reports survive, and if the live
        # room list is unavailable the restore is skipped entirely (a
        # stale backup must never win over a fresh auto-map).
        backup = await self._store.async_load()
        if (
            backup
            and self._valid_mapping(backup.get("area_mapping"))
            and isinstance(backup.get("last_seen_segments"), list)
        ):
            if live is None:
                live = await self._async_live_segments(coordinator)
            if live is not None:
                restored = self._filter_restored(backup, live)
                if restored is not None:
                    _LOGGER.info(
                        "roborock_b01: restoring segment-to-area mapping for %s "
                        "from backup (%d areas)",
                        registry_entry.entity_id,
                        len(restored["area_mapping"]),
                    )

        # 2) Otherwise rebuild from the robot's own rooms + HA areas.
        if restored is None:
            payload = await self._async_auto_map(registry_entry)
            if payload:
                automap_live = set(payload.pop("_rooms").values())
                restored = self._filter_restored(payload, automap_live)
                if restored is not None:
                    _LOGGER.info(
                        "roborock_b01: auto-mapped robot rooms to HA areas for %s: %s",
                        registry_entry.entity_id,
                        restored["area_mapping"],
                    )

        if restored is None:
            if area_mapping and live is not None:
                # Nothing usable could be rebuilt either: raise the
                # repair issue and leave the registry empty - core's
                # clean_area then reports "mapping not configured"
                # instead of sending ids the room guard would refuse.
                _LOGGER.info(
                    "roborock_b01: removed the unusable segment-to-area "
                    "mapping of %s (no mapped room exists on the robot "
                    "and no mapping could be rebuilt)",
                    registry_entry.entity_id,
                )
                self._async_report_stale(registry_entry, live)
            else:
                _LOGGER.info(
                    "roborock_b01: no restorable mapping for %s (no valid backup, "
                    "or it does not match the current rooms/areas)",
                    registry_entry.entity_id,
                )
            return
        er.async_update_entity_options(registry_entry.entity_id, "vacuum", restored)
        await self._store.async_save(restored)
        # The rebuilt mapping is verified against the robot's current
        # rooms by construction: clear any earlier staleness issue.
        self._clear_stale_issue(registry_entry)

    @staticmethod
    def _mapping_is_current(area_mapping: dict, live: set[str]) -> bool:
        """True if every mapped segment id is in the live room set."""
        mapped = {str(seg) for segs in area_mapping.values() for seg in segs}
        return mapped <= live

    @staticmethod
    def _trim_stale_segments(area_mapping: dict, live: set[str]) -> dict | None:
        """Drop mapped ids the robot no longer reports.

        Returns the trimmed mapping, or None when every mapped id is
        stale (nothing usable survives).
        """
        trimmed = {
            area_id: [s for s in segs if str(s) in live]
            for area_id, segs in area_mapping.items()
        }
        trimmed = {a: s for a, s in trimmed.items() if s}
        return trimmed or None

    def _clear_stale_issue(self, registry_entry) -> None:
        """Delete a previously raised staleness issue (no-op if absent)."""
        from homeassistant.helpers import issue_registry as ir

        ir.async_delete_issue(
            self.hass, DOMAIN, f"stale_area_mapping_{registry_entry.entity_id}"
        )

    def _async_report_stale(self, registry_entry, live: set[str]) -> None:
        """Raise a repair issue for a mapping the robot no longer matches."""
        from homeassistant.helpers import issue_registry as ir

        live_names = ", ".join(sorted(live))
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"stale_area_mapping_{registry_entry.entity_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="stale_area_mapping",
            translation_placeholders={
                "entity_id": registry_entry.entity_id,
                "valid": live_names or "none",
            },
            data={"entity_id": registry_entry.entity_id, "valid": sorted(live)},
        )

    def _filter_restored(
        self, payload: dict, live_segments: set[str] | None
    ) -> dict | None:
        """Filter a restore to known areas and the robot's live rooms.

        Drops areas deleted from HA, keeps every mapped room the robot
        still reports (even if the robot has extra new rooms), and
        returns None when nothing meaningful survives - in which case
        nothing is written anywhere.
        """
        area_ids = {
            getattr(area, "area_id", None) or str(getattr(area, "id", ""))
            for area in _async_area_registry(self.hass).async_list_areas()
        }
        mapping = payload["area_mapping"]
        filtered = {
            area_id: segs for area_id, segs in mapping.items() if area_id in area_ids
        }
        if not filtered:
            return None
        if live_segments:
            filtered = {
                area_id: [s for s in segs if s in live_segments]
                for area_id, segs in filtered.items()
            }
            filtered = {a: s for a, s in filtered.items() if s}
            if not filtered:
                return None
        return {
            "area_mapping": filtered,
            "last_seen_segments": payload.get("last_seen_segments"),
        }

    async def _async_live_segments(self, coordinator) -> set[str] | None:
        """Room ids the robot currently reports (None if unavailable)."""
        entity = self._vacuum_entity_for_coordinator(coordinator)
        if entity is None or not hasattr(entity, "get_maps"):
            return None
        try:
            response = await entity.get_maps()
        except Exception as err:
            _LOGGER.debug("roborock_b01: get_maps during restore failed: %s", err)
            return None
        live: set[str] = set()
        for map_entry in response.get("maps", []):
            for room_id in map_entry.get("rooms") or {}:
                live.add(str(room_id))
        return live or None

    # -------------------------------------------------------- auto-map

    async def _async_auto_map(self, registry_entry) -> dict | None:
        """Map robot rooms onto HA areas by name; None if impossible."""
        entity = self._vacuum_entity(registry_entry.entity_id)
        if entity is None or not hasattr(entity, "get_maps"):
            return None
        try:
            response = await entity.get_maps()
        except Exception as err:
            _LOGGER.debug("roborock_b01: auto-map get_maps failed: %s", err)
            return None
        rooms: dict[str, str] = {}  # room name -> segment id
        for map_entry in response.get("maps", []):
            for room_id, name in (map_entry.get("rooms") or {}).items():
                rooms.setdefault(str(name), str(room_id))
        if not rooms:
            return None
        last_seen = [{"id": segment, "name": name} for name, segment in rooms.items()]

        areas_by_norm: dict[str, str] = {}
        for area in _async_area_registry(self.hass).async_list_areas():
            norm = getattr(area, "normalized_name", None) or str(
                getattr(area, "name", "")
            )
            areas_by_norm[str(norm).lower()] = getattr(area, "area_id", None) or str(
                getattr(area, "id", "")
            )

        mapping: dict[str, list[str]] = {}
        for room_name, segment in rooms.items():
            area_id = self._best_area(room_name, areas_by_norm)
            if area_id:
                mapping.setdefault(area_id, []).append(segment)
        if not mapping:
            return None
        payload = _backup_payload(mapping, last_seen)
        payload["_rooms"] = rooms  # consumed (and popped) by reconcile
        return payload

    @staticmethod
    def _best_area(room_name: str, areas_by_norm: dict[str, str]) -> str | None:
        low = room_name.lower()
        best, best_len = None, 0
        for norm, area_id in areas_by_norm.items():
            if norm == low:
                return area_id
            if (norm in low or low in norm) and len(norm) > best_len:
                best, best_len = area_id, len(norm)
        return best


@callback
def async_setup_area_mapping_keeper(hass: HomeAssistant, entry) -> None:
    """Reconcile the mapping for every coordinator in the hub registry.

    Runs once now and once after entities/registries have settled (the
    second pass catches entity registration lag).
    """
    from homeassistant.helpers.event import async_call_later

    from .hub import get_coordinators

    keeper = _Keeper(hass)

    def _reconcile_all(*_):
        for coord in get_coordinators(hass, entry):
            hass.async_create_task(keeper.async_reconcile(coord))

    _reconcile_all()
    # Second pass once entities have settled.
    delayed = async_call_later(hass, _RECONCILE_DELAY, _reconcile_all)
    if entry is not None:
        entry.async_on_unload(delayed)
