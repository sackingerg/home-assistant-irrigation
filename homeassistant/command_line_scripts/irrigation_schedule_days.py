#!/usr/bin/env python3
"""Reads schedule.* weekly blocks from Home Assistant's storage and emits
each irrigation zone's active days as JSON, plus per-zone OVERLAP days.

WHY THIS EXISTS: HA's schedule.* entities only expose `next_event` (and a
few cosmetic fields) as runtime state attributes -- the actual per-weekday
block configuration is config, not state, and isn't queryable via templates
(states()/state_attr()) at all. This script reads it straight from
/config/.storage/schedule (the source of truth the UI's weekly grid editor
itself reads/writes) so a dashboard template has something to work with.

Matched by the storage item's internal "id" field (e.g. "zone_1_schedule"),
NOT by entity_id -- the internal id stays stable even if a zone's schedule
entity gets renamed via the UI's "refresh entity ID" action, which has
happened before on this system.

OVERLAP DETECTION (added 2026-07-08, GPM-aware since 2026-07-09):
For every zone and weekday, a sweep over all block boundaries finds every
moment where 2+ zones are scheduled simultaneously (property-wide -- one
shared pump). Days where a zone participates in ANY concurrency are listed
in "<zone>_ol" (blue dot). Days where the concurrent zones' combined GPM
exceeds the pump limit are ALSO listed in "<zone>_ov" (yellow-triangle dot,
takes precedence on the dashboard). The "_ov" days are what the Schedule
Grid Over-Limit Verification automation reports on: since 2026-07-27 nothing
blocks a zone at runtime, so this programming-time warning is the only
pump-limit enforcement the schedule has. Touching blocks (one ends exactly
when the next starts) are NOT overlaps. Times compare as HH:MM:SS strings.

GPM values and the pump limit arrive as a JSON argument (argv[1]) rendered
live by the command_line sensor's command template in scheduling_core.yaml:
  {"pump_max": 20.0, "zone_1": 16.0, ..., "west2_zone6": 12.0}
Without the argument, all GPM default to 0 and nothing is flagged over.

Run by the command_line sensor in packages/scheduling_core.yaml.
"""
import json
import sys

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
STORAGE_PATH = "/config/.storage/schedule"

# Matches East zones (zone_N_schedule — no "east" in the storage id, they were
# the first controller built and the bare name stuck), garden zones
# (garden_zone_N_schedule), and west-side zones (west1_zoneN_schedule,
# west2_zoneN_schedule).
# Tolerates a trailing _N suffix (e.g. zone_6_schedule_2 — HA appends one when
# a helper is deleted and recreated with the same name).
# The zone_key emitted matches the json_attributes listed in scheduling_core.yaml.
import re
_TRACKED = re.compile(
    r"^(zone_\d+|garden_zone_\d+|west1_zone\d+|west2_zone\d+)_schedule(_\d+)?$")

def _zone_key(item_id: str):
    m = _TRACKED.match(item_id)
    return m.group(1) if m else None

def _blocks(item, day):
    """Return [(from, to), ...] for a storage item's weekday, defensively."""
    out = []
    for b in item.get(day) or []:
        f, t = b.get("from"), b.get("to")
        if f and t:
            out.append((f, t))
    return out

gpm = {}
pump_max = 20.0
if len(sys.argv) > 1:
    try:
        cfg = json.loads(sys.argv[1])
        pump_max = float(cfg.pop("pump_max", 20.0))
        gpm = {k: float(v) for k, v in cfg.items()}
    except (ValueError, TypeError, AttributeError):
        pass

result = {}
try:
    with open(STORAGE_PATH) as f:
        data = json.load(f)

    zones = {}  # zone_key -> storage item
    for item in data.get("data", {}).get("items", []):
        key = _zone_key(item.get("id", ""))
        if key:
            zones[key] = item

    for key, item in zones.items():
        result[key] = [day for day in DAYS if item.get(day)]

    ol_days = {k: [] for k in zones}
    ov_days = {k: [] for k in zones}
    for day in DAYS:
        per = {k: _blocks(item, day) for k, item in zones.items()}
        per = {k: v for k, v in per.items() if v}
        if len(per) < 2:
            continue
        bounds = sorted({t for bl in per.values() for b in bl for t in b})
        day_ol, day_ov = set(), set()
        for i in range(len(bounds) - 1):
            t1, t2 = bounds[i], bounds[i + 1]
            active = [k for k, bl in per.items()
                      if any(f <= t1 and t2 <= t for f, t in bl)]
            if len(active) >= 2:
                day_ol.update(active)
                if sum(gpm.get(k, 0.0) for k in active) > pump_max:
                    day_ov.update(active)
        for k in day_ol:
            ol_days[k].append(day)
        for k in day_ov:
            ov_days[k].append(day)

    for k in zones:
        result[k + "_ol"] = ol_days[k]
        result[k + "_ov"] = ov_days[k]
except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
    pass

print(json.dumps(result))
