# Adapting this to your property

Everything in this repo describes one specific property: four controllers, 21 zones, one
20 GPM well pump. None of those numbers are load-bearing, but the zone names and entity IDs
are threaded through the packages, the dashboard and the reactor maps. This is the order to
change them in so nothing silently breaks.

Budget an evening. The mechanical parts are easy; the survey is what takes time.

---

## 0. Survey first — do not skip this

Before touching any config, for **each** controller location:

1. Photograph the terminal block: labels, wire colours, COM wiring, transformer rating.
2. Trace which zone each station actually waters, physically, by running it.
3. Count the heads per zone and note the type (rotor / spray / drip).

On the property this repo came from, the original labelling was wrong at two of four
locations — one "5-zone" controller was actually 6 with two names swapped, and an
"11-zone" location needed two boards. Every hour spent surveying saved several later.

Estimate GPM per zone: rotor heads run 3–4 GPM each, sprays around 1.5–2, drip much less.
You do not need a flow meter to start — the figures live in editable helpers.

---

## 1. Decide your naming scheme, once

Every entity in the system follows one pattern:

```
<domain>.<area>_<system>_zone_<N>_<role>
```

`<area>` is your Home Assistant area name, which HA prepends automatically to entities
assigned to that area. In this repo the area is `outside`, so everything reads
`input_boolean.outside_west1_zone_3_enabled`.

**Pick `<system>` names before you write anything.** Changing one later means touching that
controller's helper package, the zone registry, five reactor maps, the manual-control zone
map, the action log's label table, and the dashboard. This repo uses `irrigation` (for the
East controller), `garden`, `west1`, `west2` — and the East one is a wart: it should have
been `east`, but it was the first controller built and the bare name stuck. Learn from that
and name them all consistently on day one.

Per zone you will have:

| Role | Entity |
|---|---|
| schedule enable | `input_boolean.<area>_<sys>_zone_N_enabled` |
| Run Now duration | `input_number.<area>_<sys>_zone_N_duration` |
| GPM estimate | `input_number.<area>_<sys>_zone_N_gpm` |
| countdown timer | `timer.<area>_<sys>_zone_N_timer` |
| weekly schedule | `schedule.<area>_<sys>_zoneN_schedule` |

Plus one master toggle and one status text per controller.

---

## 2. Firmware

Start from the controller config closest to your channel count
(`esphome/irrigation-west1-controller.yaml` for a 6-zone board).

- **Generate your own credentials.** Every key and password in this repo is a
  `REPLACE_WITH_YOUR_OWN_…` placeholder. Copy `esphome/secrets.yaml.example` to
  `secrets.yaml`, and generate an API key with `openssl rand -base64 32`.
- Rename the node and each zone switch.
- Keep `restore_value: false` on the duration numbers and `restore_mode: ALWAYS_OFF` on the
  switches. Both matter: together they guarantee a rebooted controller comes back with every
  valve shut and refuses to reopen until Home Assistant re-arms it.
- **Adjust the interlock to your pump.** The `others >= 2` lambda allows two zones per
  board. If your zones are large relative to your supply, make it `>= 1`. If your pump is
  generous, raise it. Every zone's `on_turn_on` block has its own copy — change them all.

After flashing, check the generated entity IDs in Settings → Devices. They come from the
node name plus the switch name and are easy to get subtly wrong.

---

## 3. The zone registry — the one place that matters most

`homeassistant/packages/irrigation_zone_engine.yaml` holds a single table mapping
`"<system>:<zone>"` to that zone's seven entities:

```yaml
"west1:3": {switch: switch.…_zone_3_south_center_c,
            duration: input_number.outside_west1_zone_3_duration,
            timer: timer.outside_west1_zone_3_timer,
            device: number.…_west1_controller_zone_3_duration,
            enable: input_boolean.outside_west1_zone_3_enabled,
            gpm: input_number.outside_west1_zone_3_gpm,
            label: "Z3 · South Center C"}
```

All four generic scripts read this same table through a YAML anchor, so there is exactly
one definition. Get this table right and most of the system works; get a row wrong and that
one zone misbehaves in an obvious, traceable way.

Also update `sys_zone_numbers`, `sys_status` and `sys_label` in the same file if you are
adding or renaming a controller.

---

## 4. Helper packages

Copy one controller package per controller. These now contain **helpers only** — no scripts.
Edit:

- helper names and counts (`input_boolean`, `input_number`, `timer`, `input_text`)
- the header comment, which documents that controller's wiring

---

## 5. The scheduler

In `scheduling_core.yaml`, for **every** zone, add an entry to:

1. the reactor's trigger `entity_id` list
2. `all_zone_schedules`
3. `zone_switch_map`
4. `zone_enable_map`
5. `zone_master_map`
6. `zone_system_map`
7. `zone_number_map`

and to the command_line sensor's JSON argument at the bottom of the file, plus its
`json_attributes` list (three entries per zone: bare, `_ol`, `_ov`).

A missing map entry doesn't error — the zone just silently never waters. After editing,
confirm every map has the same number of entries as `all_zone_schedules`.

Also update:

- `irrigation_gpm_monitor.yaml` — GPM helpers, and each system sensor's zone list **twice**
  (the state template and the `zones` attribute template; state-based template entities
  can't share variables)
- `irrigation_zone_engine.yaml` — `zone_registry`, `sys_zone_numbers`, `sys_status`,
  `sys_label`, `sys_gpm_sensor`
- `irrigation_action_log.yaml` — the trigger list and the `labels` table
- `irrigation_schedule_days.py` — the `_TRACKED` regex, if your schedule storage ids don't
  match the existing patterns

---

## 6. Schedule helpers

Create these **in the UI**, never in YAML — a YAML-defined schedule opens in the weekly grid
editor but its "add block" control is disabled.

To get the area prefix onto the entity ID:

1. Create the helper with a plain name ("West 1 Zone 1 Schedule") and **save**
2. **Reopen** it, assign the area, save again
3. **Reopen** once more and use **refresh entity ID**

The prefix comes from the area, not from the typed name — naming it "Outside West 1 Zone 1"
gives you `outside_outside_west_1…`.

Once the IDs are wired into `scheduling_core.yaml`, **do not touch "refresh entity ID"
again**. It renames silently and the reactor stops controlling that zone with no error.

---

## 7. Dashboard

`homeassistant/dashboards/irrigation.yaml` needs [button-card](https://github.com/custom-cards/button-card)
from HACS. Expect to spend the most time here — it is the least generic part of the repo.

Every button now passes a controller key and a zone number, so the call sites are uniform:

```yaml
tap_action:
  action: call-service
  service: script.irrigation_run_zone
  service_data:
    system: west1
    zone: 3
```

- Replace every entity ID (a careful find-and-replace per system works)
- **Supply your own zone map image.** The `picture-elements` cards reference
  `/local/property_sprinkler_map.jpg`, which is not included. Drop any overhead image of
  your property in `/config/www/` and re-place the zone dots — their `top:`/`left:`
  percentages are meaningless against a different image.
- Zone section tinting uses the native sections `background:` option (colour + opacity).
  No `card-mod` needed.

If you'd rather start small, take just the per-zone card block and the Weekly Pattern
markdown; they're the two most useful pieces standalone.

---

## 8. Tune the flow budget

Set `input_number.<area>_pump_max_gpm` to your pump's rating and fill in every zone's GPM
from the Zone Config view.

**A zone left at 0 will silently under-report the total and sail through the Run Controller
pre-flight.** If you add a zone later, set its GPM before you schedule it.

Then program a week of blocks and watch the Weekly Pattern grid: yellow triangles mark
zone/day combinations where the *programmed* simultaneous total exceeds your pump. Stagger
those blocks until the triangles are gone.

---

## What you can drop

The repo is modular. Safe to leave out entirely:

| File | If you don't want… |
|---|---|
| `irrigation_action_log.yaml` | run history (optional — but it's the best debugging tool here) |
| `irrigation_gpm_monitor.yaml` | flow budgeting; the Run Controller pre-flight needs it |

The irreducible core is: `irrigation_zone_engine.yaml` + one controller's helper package +
`scheduling_core.yaml` + the schedule helpers. That alone gives you working per-zone weekly
scheduling with firmware-enforced shutoff.

---

## Things that will bite you

- **A missing reactor map entry doesn't error.** The zone just never waters. Count your map
  entries against `all_zone_schedules`.
- **Never end a run with `delay` then `switch.turn_off`.** In a `mode: parallel` script that
  arms a shutoff which outlives its own run and cuts short the *next* one. Wait for the
  valve instead. This repo learned that the expensive way — see README §3.
- **`schedule.*` state is `on` only inside a block.** It does not mean "enabled" — that's
  what the separate master and per-zone booleans are for.
- **Templates need defaults.** `| int(5)`, `| float(0)` — an unavailable entity otherwise
  crashes the whole script mid-run, potentially with a valve open.
- **Motorised valves close slowly.** If yours take >15 s, keep the interlock-retry logic in
  `scheduling_core.yaml`; without it a zone starting as its predecessor closes gets bounced
  and silently doesn't water.
- **`ha core check` may not work from the SSH add-on** (no supervisor token). Validate YAML
  locally before pushing, then reload from the UI and watch the log.
