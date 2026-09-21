"""Shared watcher for B01 coordinators of the core Roborock integration.

Both the map cameras and the area-mapping keeper need the same thing:
"call me for every B01 coordinator, present and future". Coordinators
appear when the core Roborock entry loads its runtime_data - which may
be before or after our own setup - so discovery is: immediate scan,
one delayed re-scan, and a dispatcher listener per core entry for
late arrivals (deduped by duid).
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later

from .const import LATE_SCAN_DELAY

_ROBOROCK_DOMAIN = "roborock"


@callback
def async_watch_b01_coordinators(
    hass: HomeAssistant,
    on_coordinator,
    entry=None,
    dedupe: bool = True,
) -> None:
    """Invoke ``on_coordinator`` for every B01 coordinator, now and future.

    ``on_coordinator(coord)`` is called once per duid when ``dedupe`` is
    true (entity-style consumers); with ``dedupe=False`` every
    appearance of a coordinator - including the same duid after the
    core entry was removed and re-added - is delivered (reconcilers).
    When ``entry`` is given, all listener lifetimes are bound to its
    unload.
    """
    known: set[str] = set()
    watched_entries: set[str] = set()

    @callback
    def _deliver(coord) -> None:
        if dedupe:
            if coord.duid in known:
                return
            known.add(coord.duid)
        on_coordinator(coord)

    @callback
    def _scan(_now=None) -> None:
        for rob_entry in hass.config_entries.async_entries(_ROBOROCK_DOMAIN):
            if rob_entry.entry_id not in watched_entries:
                watched_entries.add(rob_entry.entry_id)
                if entry is not None:
                    entry.async_on_unload(
                        async_dispatcher_connect(
                            hass,
                            f"roborock_coordinator_added_{rob_entry.entry_id}",
                            _deliver,
                        )
                    )
            coordinators = getattr(rob_entry, "runtime_data", None)
            if coordinators is None:
                continue
            # Old HA cores have no B01 coordinator lists; the vacuum-patch
            # guard already logged that room/map features are disabled.
            coords = list(getattr(coordinators, "b01_q7", ()) or ()) + list(
                getattr(coordinators, "b01_q10", ()) or ()
            )
            for coord in coords:
                _deliver(coord)

    _scan()
    # Re-scan once in case the core entry finished after us.
    remove_rescan = async_call_later(hass, LATE_SCAN_DELAY, _scan)
    if entry is not None:
        entry.async_on_unload(remove_rescan)
