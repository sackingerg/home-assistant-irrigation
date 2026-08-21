# System Reference

**Status: in service.** 4 ESPHome controllers · 21 sprinkler zones · 1 shared well pump.
Home Assistant OS, HA core 2026.8.x, packages-based config.

This is the complete reference for how the system works, what every entity is, and which
file owns it. It documents one real deployment — zone names, GPM figures and entity IDs are
specific to that property. See [ADAPTING.md](ADAPTING.md) for what to change to run it on
your own.

> The property also has a rainwater tank and drip-irrigation controller that coordinates
> with these sprinklers. It is a separate subsystem on its own hardware and is **not part
> of this repository**.

*Last verified against the live box: 2026-08-20 (entity registry, live states and recorder
history). Firmware 2.02 on all four controllers; `irrigation_zone_engine.yaml` at 1.07,
`irrigation_action_log.yaml` at 1.03, `dashboards/irrigation.yaml` at 1.01.*

---

## 1. Purpose

Water the whole property on a weekly schedule, from Home Assistant, without a
sprinkler timer box — and make it obvious at a glance what is running, what is
scheduled, and what has gone wrong.

Three things drove the design:

1. **One well pump, ~20 GPM.** Every zone on the property draws from it. Nothing
   in the system is allowed to quietly exceed that; equally, nothing is allowed to
   quietly *prevent* it — see §7.
2. **A dead Home Assistant must not leave a valve open.** HA arms a run; the ESP32
   firmware ends it. That split is the single most important rule in the system.
3. **Changing when a zone waters should be one edit.** Widening a schedule block is
   the whole operation — there is no separate "duration" to keep in sync.

---

## 2. System at a glance

| Controller | ESPHome node | Zones | Covers | Old hardware it replaced |
|---|---|---|---|---|
| **East** | `irrigation-east` | 6 | Front driveway, east property lines, flower bed, patio | first board built; original 6-zone timer |
| **Garden** | `irrigation_garden_controller` | 3 | Strawberries, berry bed, raised-bed drippers | — |
| **West 1** | `irrigation_west1_controller` | 6 | South and west lawn | Rain Bird stations 1–6 |
| **West 2** | `irrigation_west2_controller` | 6 | North and far-west lawn | Rain Bird stations 7–12 |

All four are **ESP32-S3** boards (Waveshare 6-channel relay modules), ESP-IDF framework,
area **Outside**. All four use the same relay pin map:

| Zone | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| GPIO | 1 | 2 | 41 | 42 | 45 | 46 |

GPIO45 and GPIO46 are strapping pins; both carry `ignore_strapping_warning: true` and are
safe as relay outputs. Garden uses only the first three channels.

### Zone inventory

GPM figures are the live values of the `input_number.*_gpm` helpers, not constants.

**East** — master `input_boolean.outside_irrigation_schedule_enabled`

| Z | Name | Switch entity (`switch.`) | GPM | Run Now default |
|---|---|---|---|---|
| 1 | Front Driveway | `outside_irrigation_east_zone_1_front_driveway` | 16 | 30 min |
| 2 | East Property Line | `outside_irrigation_east_zone_2_east_property_line` | 16 | 30 min |
| 3 | Driveway East | `outside_irrigation_east_zone_3_driveway_east` | 16 | 30 min |
| 4 | Southeast Property Line | `outside_irrigation_east_zone_4_southeast_property_line` | 16 | 30 min |
| 5 | East Side Flower Bed | `outside_irrigation_east_zone_5_east_side_flower_bed` | 15 | 30 min |
| 6 | East Patio | `outside_irrigation_east_zone_6_east_patio` | 5 | 15 min |

**Garden** — master `input_boolean.outside_garden_schedule_enabled`

| Z | Name | Switch entity (`switch.`) | GPM | Run Now default |
|---|---|---|---|---|
| 1 | Strawberries | `outside_irrigation_garden_controller_zone_1_strawberries` | 3 | 4 min |
| 2 | Blueberry-Blackberry Bed | `outside_irrigation_garden_controller_zone_2_blueberry_blackberry_bed` | 10 | 5 min |
| 3 | Dripper System | `outside_irrigation_garden_controller_zone_3_dripper_system` | 10 | 40 min |

**West 1** — master `input_boolean.outside_west1_schedule_enabled`

| Z | Name | Switch entity (`switch.`) | GPM |
|---|---|---|---|
| 1 | South Center A | `outside_irrigation_west1_controller_zone_1_south_center_a` | 16 |
| 2 | South Center B | `outside_irrigation_west1_controller_zone_2_south_center_b` | 16 |
| 3 | South Center C | `outside_irrigation_west1_controller_zone_3_south_center_c` | 10 |
| 4 | Southwest | `outside_irrigation_west1_controller_zone_4_southwest` | 16 |
| 5 | West Center | `outside_irrigation_west1_controller_zone_5_west_center` | 10 |
| 6 | Far West Edge | `outside_irrigation_west1_controller_zone_6_far_west_edge` | 16 |

**West 2** — master `input_boolean.outside_west2_schedule_enabled`

| Z | Name | Switch entity (`switch.`) | GPM |
|---|---|---|---|
| 1 | Near Shed | `outside_irrigation_west2_controller_zone_1_near_shed` | 16 |
| 2 | North of House | `outside_irrigation_west2_controller_zone_2_north_of_house` | 16 |
| 3 | North Top Edge | `outside_irrigation_west2_controller_zone_3_north_top_edge` | 16 |
| 4 | Far West | `outside_irrigation_west2_controller_zone_4_far_west` | 16 |
| 5 | Station 11 | `outside_irrigation_west2_controller_zone_5_station_11` | 16 |
| 6 | Center West | `outside_irrigation_west2_controller_zone_6_center_west` | 12 |

> **West 2 Zone 5 "Station 11"** still carries its old station number instead of an area
> name — the physical area it waters was never confirmed against the property map.

---

## 3. Firmware — what the ESP32 does

The firmware is the **single source of shutoff truth**. HA arms a run and opens the valve;
the ESP32 closes it. Nothing in the shutoff path waits on HA, so a zone still auto-offs with
Home Assistant stopped, unreachable or powered down.

All 21 zones across all four boards run **one shared definition**,
`esphome/zone_timer.yaml`, included once per zone through each controller's `packages:`
block and parameterised only by the zone's id and label:

```yaml
packages:
  zone3_timer: !include {file: zone_timer.yaml, vars: {zid: zone3, zlabel: "Zone 3"}}
```

### Opening a valve

`on_turn_on` applies two gates, then hands the countdown to a script:

```yaml
on_turn_on:
  - if:
      condition:                      # 1. INTERLOCK: max 2 zones per board
        lambda: 'return others >= 2;' # (enumerates the OTHER zones, excludes self)
      then:
        - switch.turn_off: zone3      # rejected — pulses the switch on→off
      else:
        - if:
            condition:                # 2. DURATION GATE: refuse an unarmed zone
              lambda: 'return id(zone3_duration).state <= 0;'
            then:
              - switch.turn_off: zone3
            else:
              - script.execute: zone3_autooff
on_turn_off:
  - script.stop: zone3_autooff        # a stop must not leave a countdown armed
  - lambda: 'id(zone3_end_ms) = 0;'
  - component.update: zone3_remaining
```

### The countdown is restartable

```yaml
script:
  - id: zone3_autooff
    mode: restart                     # ← cancels any pending countdown first
    then:
      - lambda: 'id(zone3_end_ms) = millis() + (uint32_t)(id(zone3_duration).state * 60000);'
      - component.update: zone3_remaining
      - delay: !lambda 'return (uint32_t)(id(zone3_duration).state * 60000);'
      - switch.turn_off: zone3

number:
  - platform: template
    id: zone3_duration
    on_value:
      - if:
          condition:
            and:
              - switch.is_on: zone3   # ignore the arm-before-open write
              - lambda: 'return x > 0;'   # a disarm is not a reset
          then:
            - script.execute: zone3_autooff
```

**Writing a new duration to a running zone restarts its countdown in place** — the valve is
never closed and reopened. Both guards on `on_value` are load-bearing: without `switch.is_on`
the pre-open arming write would start the countdown early, and without `x > 0` the stop
path's disarm would be read as a reset.

`mode: restart` also guarantees a zone can never hold two deadlines at once, which is what
made the previous inline-`delay:` design unsafe to reset. See §10 stage 12.

### Consequences worth knowing

- **A zone will not open unless HA has just written a duration > 0.** Duration numbers are
  `restore_value: false`, so they read 0 after every reboot. A controller that restarts on
  its own comes back with all valves shut and cannot reopen one until HA re-arms it.
- **Rejection looks like a brief on→off pulse**, not an error. HA detects it by checking
  whether the switch is actually `on` a few seconds after the start command (§5).
- **Relays are `restore_mode: ALWAYS_OFF`.** Power loss closes everything.
- **The interlock is per-board.** West 1 cannot see West 2's zones. Property-wide flow is an
  HA concern, not a firmware one.

### What each board reports

| Entity | Purpose |
|---|---|
| `sensor.…_zone_N_time_remaining` | seconds left on the **firmware's** countdown — the authoritative figure. 60 s poll, `delta: 1` filter so an idle zone is silent, plus an immediate publish on start, reset and stop. |
| `sensor.…_config_revision` | the firmware version actually on the chip, e.g. `2.02` |
| status LED, WiFi signal, uptime, ESPHome version | diagnostics |

`config_revision` exists because a header comment describes the *file*, not the *chip*. On
2026-08-17 three installs in a row silently flashed an old build and nothing reported it;
the Overview header now reads all four and flags any mismatch.

**HA never sends `switch.turn_off` unless the operator asked for it.** The only signals for a
run are `number.set_value` (duration) then `switch.turn_on`. `turn_off` comes from the stop
scripts and from nowhere else — no timeout, no backstop, no second opinion about when a run
should end. The ESP32 owns shutoff completely, and the one time HA was allowed a deadline of
its own it used it to truncate a healthy run (§10, Stage 15).

---

## 4. Scheduling model — the block *is* the runtime

Each of the 21 zones has its own UI-created `schedule.*` helper with a weekly block grid.

> **A block of 08:00–08:15 waters that zone for 15 minutes.**
> Want 20 minutes when it warms up? Widen the block to 08:00–08:20. That is the entire
> operation. The "Run Now Duration" `input_number` is **not** consulted for scheduled runs.

Schedule helpers **must** be created through the UI (Settings → Helpers → Schedule). A
YAML-defined schedule opens in the weekly editor but its "add block" control is disabled.

Two gates sit in front of every scheduled run:

| Gate | Entity | Effect when off |
|---|---|---|
| Controller master | `input_boolean.outside_<sys>_schedule_enabled` | that whole controller stops starting scheduled runs |
| Per-zone enable | `input_boolean.outside_<sys>_zone_N_enabled` | that one zone is skipped; the rest still run |

A `schedule.*` entity's state is `on` only *inside* a block — it does **not** mean
"enabled". That is why the separate master/enable booleans exist.

### Schedule entity IDs

These are irregular for historical reasons. **The IDs below are the live ones**, verified
against the entity registry on 2026-08-07:

| System | Schedule entity IDs |
|---|---|
| East | `schedule.outside_zone_1_schedule_front_driveway`, then `schedule.outside_zone_2_schedule` … `outside_zone_6_schedule` (no "east" in the id) |
| Garden | `schedule.outside_garden_zone_1_schedule` … `_3_schedule` |
| West 1 | `schedule.outside_west1_zone1_schedule` … `zone6_schedule` (**no underscore before the digit**) |
| West 2 | `schedule.outside_west2_zone1_schedule` … `zone6_schedule` (same) |

---

## 5. How a zone actually runs

There are exactly three ways a valve opens. All three converge on the per-controller
`script.irrigation_run_zone`, the single implementation shared by all four controllers
(`packages/irrigation_zone_engine.yaml`). It arms the ESP32 and opens the valve.

### Path A — scheduled (normal nightly operation)

```
schedule.outside_<zone>_schedule  →  ON
   ↓
automation.schedule_reactor                      [scheduling_core.yaml, mode: queued/25]
   resolves the zone through 5 maps (switch, enable, master, script, number)
   requires: master ON + zone enable ON + valve currently off + block minutes left > 0
   run_minutes = minutes remaining in the block, capped at 240
   ↓
script.outside_irrigation_retrying_zone_start    [parallel, max 25]
   calls script.irrigation_run_zone (system, zone)
   waits 5 s, then checks the valve really opened
   if NOT on  →  log warning, wait 25 s more, retry ONCE with runtime −1 min
                 still not on  →  error log + persistent notification + red log entry
   ↓
script.irrigation_run_zone  (system, zone)
   number.set_value (duration)  →  switch.turn_on  →  ESP32 auto-offs at block end
```

**The interlock retry** exists because motorised valves can take >15 s to close, so a zone
starting exactly as its predecessor closes can be bounced by the max-2 backstop. One retry
at 30 s, runtime trimmed by a whole minute so the run still ends by block end. If the retry
also fails, two zones on that board are genuinely running and it gives up loudly.

An **unavailable** switch (controller offline) takes the same path, and every message names
which cause it was — offline vs interlock.

### Path B — Run Now (one zone, spot watering)

Dashboard zone card → `script.irrigation_run_zone` (system, zone) with that zone's
`input_number.outside_<sys>_zone_N_duration` minutes. `mode: parallel`, one timer per zone,
so concurrent Run Nows do not race. Ignores the master and the per-zone enable — it is a
deliberate manual act.

**Pressing Run Now on a zone that is already running RESETS its countdown** to the current
slider value, without closing the valve. Press at 0:00 with 15 min set, press again at 13:00
with 3 min set, and the zone closes 3 minutes later. It can shorten a run as well as extend
one — the slider value is taken as the new runtime, not added to the old.

That works because the duration write reaches the firmware, whose `on_value` restarts its
`mode: restart` countdown in place (§3). Before 2026-08-17 the same press did nothing at
all: the valve was already open, so `on_turn_on` never re-fired and the number was inert.

**Two guards run before the controller is contacted:**

| Guard | Behaviour |
|---|---|
| **Scheduled run in progress** | The press is **ignored entirely** — no duration write, no `turn_on`. The scheduled runtime is adhered to exactly, because consecutive scheduled zones depend on each block ending when it said it would. Detected statelessly: valve on **and** that zone's block active **and** its master armed **and** the zone enabled. Reported on the controller's status line and in the Action Log, never silent. |
| **Over the pump limit** | Warns and **runs anyway** — notification, system log, ⚠️ Action Log entry. Monitor, not guard (§7c). |

A manual run started in the minute or two *before* its own block opens is indistinguishable
from a scheduled one and will be treated as scheduled. Deliberate: it errs toward protecting
the schedule.

### Path C — Run Controller (whole controller, manual)

Dashboard "Run Controller" button → `script.irrigation_run_controller` (field `system`).

1. **Gallon pre-flight** (§7) — if any enabled zone would exceed the pump cap, it names
   every offender and runs **nothing**.
2. Otherwise hands off to `script.irrigation_sequential_run` (system), which walks zones 1→6 in numerical
   order, **skipping any whose enable boolean is off**, each for its own Run Now duration,
   waiting for each ESP32 auto-off before starting the next.

`irrigation_sequential_run` is `mode: parallel, max 4` — one slot per controller, so one
controller's sequence never blocks another's button press.

### Restart safety

On `homeassistant: start`, the reactor sweeps all 21 schedules and resumes any zone that is
still inside its block (and enabled, and master on, and valve not already open) **for the
remaining block time only**. A reboot mid-block finishes the run on schedule rather than
restarting it.

Everything else is deliberately non-restoring: ESP32 durations (`restore_value: false`) and
all 21 HA countdown timers (`restore: false`). A reboot never resurrects a stale run.

### Stopping

| Control | Script | Scope |
|---|---|---|
| Zone card "Stop" | `script.irrigation_stop_zone` | that one valve: closes it, cancels the countdown, zeroes the controller duration |
| Page "Stop All" | `script.irrigation_stop_controller` | one controller: halts its sequence, then the same three actions on every zone |
| Overview "STOP ALL IRRIGATION" | `script.irrigation_stop_all_systems` | all four controllers |

`irrigation_stop_all_systems` kills the sequencers **first** — otherwise a runner mid-step
would reopen a valve just as it was being closed — then calls each per-system stop.

> **It does not disarm the masters.** Stop All stops what is *running*; tonight's scheduled
> run still happens. Disarming is a separate deliberate act via the Automation button.

---

## 6. Safety model

Layered, outermost first. Only two layers actually *prevent* anything.

**Every layer that closes a valve is in firmware.** Home Assistant holds no deadline, no
timeout and no failsafe; it arms a run and then only ever closes a valve because the operator
pressed Stop. This is a hard rule, not an oversight — see §10 Stage 15 for the run it cost to
learn it, and §12 for what the rule gives up.

| Layer | Where | Enforces? | What it does |
|---|---|---|---|
| ESP32 auto-off | firmware | **yes** | closes the valve after N minutes regardless of HA. `mode: restart`, so re-arming replaces the deadline rather than adding a second one |
| ESP32 duration gate | firmware | **yes** | refuses to open a zone HA has not armed |
| ESP32 max-2 interlock | firmware | **yes** | ≤2 zones per board; rejects the 3rd |
| Run Controller pre-flight | `irrigation_zone_engine.yaml` | **yes** (manual sequences only) | refuses a whole sequence that would exceed the pump cap |
| Scheduled-run guard | `irrigation_zone_engine.yaml` | **yes** (protects, never waters) | a manual press cannot disturb a zone running from its block |
| Single-run GPM warning | `irrigation_zone_engine.yaml` | no — warns | reports projected pump load on every manual start |
| Grid over-limit verification | `scheduling_core.yaml` | no — warns | flags over-limit *programming* whenever the grid changes |
| GPM monitor + banner | `irrigation_gpm_monitor.yaml` | no — warns | live total, red banner, persistent notification above cap |
| Interlock retry | `scheduling_core.yaml` | n/a | a start *guarantee*, not a safety check |

**No push notifications anywhere in irrigation.** Watering runs at night with the phone in
silent mode, so pushes cannot reach anyone. Problems surface through persistent
notifications, the system log, the Irrigation Action Log, and the per-system status text.
A push once lived in the GPM package pointing at `notify.mobile_app_<my_phone>` — a
service that has never existed on this box — and errored on every over-limit event.

---

## 7. The pump budget — four different mechanisms

One well pump, `input_number.outside_pump_max_gpm` = **20 GPM**. Four mechanisms watch it
and they are easy to confuse. **Only one ever refuses to water.**

### 7a. Live monitor — *observes*

`irrigation_gpm_monitor.yaml` sums `input_number.outside_<sys>_zone_N_gpm` for every zone
whose switch is on:

- `sensor.outside_irrigation_east_gpm` / `_garden_gpm` / `_west1_gpm` / `_west2_gpm`
- `sensor.outside_irrigation_total_gpm` — sum of the four, with a `zones` attribute listing
  every zone, its GPM and its on/off state
- `binary_sensor.outside_irrigation_over_pump_limit` — on above the cap, 60 s `delay_on`
- `automation.irrigation_over_20_gpm_warning` — persistent notification, auto-dismissing

**This never stops a zone.** Running past 20 GPM deliberately during tuning is allowed; the
system's job is to make it unmissable.

### 7b. Grid verification — *warns at programming time*

Runtime GPM blocking was **removed on 2026-07-27**. At exact block boundaries (one block
ending 22:30:00, the next starting 22:30:00) the two schedule entities update in arbitrary
callback order, and the old gate sometimes counted the *ending* zone as still on — West 1
Zone 3 was skipped that way over a 17 ms stale read. The pump recovers from brief
over-limit during switch-over, so runtime blocking was all risk and no benefit.

Enforcement moved to programming time. `command_line_scripts/irrigation_schedule_days.py`
reads `/config/.storage/schedule` directly (HA exposes only `next_event` as state — the
weekly block config is not queryable from templates at all) and sweeps every block boundary
for each weekday, producing three attribute lists per zone on
`sensor.irrigation_schedule_days`:

| Suffix | Dashboard mark | Meaning |
|---|---|---|
| *(none)* | 🔴 red | zone is scheduled that day |
| `_ol` | 🔵 blue | overlaps another zone that day |
| `_ov` | ⚠️ yellow | the overlap's **combined GPM exceeds the cap** |

`automation.irrigation_schedule_grid_over_limit_verification` raises one persistent
notification listing every `_ov` zone/day, and auto-dismisses it once the grid is fixed.
**Zones still run an over-limit grid** — fix the grid, nothing will stop them at runtime.

### 7c. Run Controller pre-flight — *blocks manual sequences*

The only mechanism that refuses to water. Because the sequential runners open exactly one
zone at a time, each step is priced as:

```
(GPM already flowing on the OTHER three controllers) + (this zone's GPM)   >   pump max ?
```

Every enabled zone is checked before anything opens. Any offender makes the whole sequence
abort: a persistent notification names each conflicting zone with its GPM and the total it
would have hit (dismiss = the OK button), plus a `system_log` warning and an Action Log
entry. Nothing is watered and nothing is queued.

With most zones at 16 GPM against a 20 GPM cap, the practical behaviour is: **a sequence
runs cleanly on its own, and blocks whenever another controller is already watering.** That
is intended.

A concurrent *Run Now* on the same controller is the one overlap this does not model — the
zone being priced is the runner's own next step.

---

### 7d. Single-run GPM warning — *reports, never refuses*

Every **manual** single-zone start now prices itself against the pump and writes the result
into that controller's status line:

```
Z3 · South Center C running (10.0 min) · 26.0 GPM ⚠ over 20.0
```

Over the limit adds a persistent notification (headed *"Over pump limit — still running"*),
a `system_log` warning and a ⚠️ Action Log entry — and then **the zone runs**. Nothing is
stopped. The firmware's max-2-per-board interlock remains the only runtime refusal.

A zone that is already open is already counted in the property total, so a **reset does not
double-count** its own draw.

**Scheduled starts deliberately do not warn.** Over-limit *programming* is already caught by
7b at edit time, so a notification per scheduled zone start would arrive nightly, unread,
for a condition already reported — and it would reintroduce exactly the runtime noise the
2026-07-27 policy removed. The status line still shows the figure; only the notification is
suppressed.

Before 2026-08-17 a manual single-zone run had **no** GPM check of any kind — you could
start a 16 GPM zone while another controller ran 16 GPM and nothing said a word.

## 8. Dashboard

One YAML-mode dashboard, `/config/dashboards/irrigation.yaml`, registered as
`irrigation-all` (sidebar: **Irrigation**). Six views:

| View | Path | Contents |
|---|---|---|
| Overview | `/irrigation-all/overview` | GPM strip, per-system status + next event, **STOP ALL IRRIGATION**, navigation |
| Irrigation East Scheduling | `/east` | full control for the 6 east zones |
| Garden Scheduling | `/garden` | full control for the 3 garden zones |
| Irrigation West 1 | `/west1` | full control for the 6 west-1 zones |
| Irrigation West 2 | `/west2` | full control for the 6 west-2 zones |
| Zone Config | `/zone-config` | pump limit, per-zone GPM editor, Irrigation Action Log, System State |

Each controller page has the same shape: GPM strip → status + **Automation ON/OFF** +
**Run Controller** + **Stop All** → Current Schedule list + Weekly Pattern → zone map →
one tinted section per zone (header button, Schedule Enabled, timer, Run Now Duration, Run
Now, Stop).

Conventions that matter here:

- **Markdown cards strip inline CSS.** Colour-coding uses emoji (🔴/⚫/🟢/🔵/⚠️), never
  `style=` attributes.
- **Zone grouping uses the native section `background:`** (colour + opacity), not a border —
  HA sections have no border option and `card-mod` is not installed. Each zone is tinted in
  its own accent colour.
- `custom:button-card` (HACS) is required for nearly every control.
- The **only** actuator on the Overview is the global stop. Everything that targets a single
  system lives on that system's page.

---

## 9. Code reference — every file and what it owns

### Home Assistant packages (`/config/packages/`)

| File | Owns |
|---|---|
| `irrigation_east.yaml` | East **helpers only**: 1 master + 6 enable booleans, 6 durations, status text, 6 timers |
| `irrigation_garden.yaml` | Garden helpers, 3 zones |
| `irrigation_west1.yaml` | West 1 helpers, 6 zones |
| `irrigation_west2.yaml` | West 2 helpers, 6 zones |
| `irrigation_zone_engine.yaml` | **The zone registry and all six generic scripts.** Every controller behaviour lives here — `irrigation_run_zone`, `irrigation_stop_zone`, `irrigation_stop_controller`, `irrigation_sequential_run`, `irrigation_run_controller` (gallon pre-flight), `irrigation_stop_all_systems` |
| `scheduling_core.yaml` | `automation.schedule_reactor` (all 21 zones, 5 maps), `script.outside_irrigation_retrying_zone_start`, weekly-pattern refresh, grid over-limit verification, the `sensor.irrigation_schedule_days` command_line sensor |
| `irrigation_gpm_monitor.yaml` | 21 GPM helpers + pump max, 4 per-system GPM sensors, total, over-limit binary sensor, over-limit warning automation, GPM editor selector |
| `irrigation_action_log.yaml` | `sensor.outside_irrigation_action_log` — rolling 40-entry history |

### Other files

| File | Purpose |
|---|---|
| `dashboards/irrigation.yaml` | the whole dashboard (6 views) |
| `command_line_scripts/irrigation_schedule_days.py` | reads `.storage/schedule`, emits per-zone active/overlap/over-limit days |
| `esphome/zone_timer.yaml` | **The shared zone definition** — duration number, `mode: restart` countdown script, deadline global, Time Remaining sensor. Included once per zone by every board, 21 times total. One copy of the timing logic for the whole property. |
| `esphome/irrigation-east.yaml` | East firmware — node identity, pin map, interlock, 6 `zone_timer` includes |
| `esphome/irrigation_garden_controller.yaml` | Garden firmware |
| `esphome/irrigation-west1-controller.yaml` | West 1 firmware |
| `esphome/irrigation-west2-controller.yaml` | West 2 firmware |

### Scripts

| Script | Mode | Called by |
|---|---|---|
| `irrigation_run_zone` (system, zone) | parallel (21) | Run Now, zone-map dots, the retrying starter for every scheduled run, and each sequential step |
| `irrigation_sequential_run` (system) | parallel (4) | **manual only** — `irrigation_run_controller` |
| `irrigation_stop_zone` (system, zone) | parallel (21) | per-zone Stop button |
| `irrigation_stop_controller` (system) | parallel (4) | page Stop All; `irrigation_stop_all_systems` |
| `outside_irrigation_retrying_zone_start` | parallel (25) | `automation.schedule_reactor` |
| `irrigation_run_controller` | parallel (4) | "Run Controller" button |
| `irrigation_stop_all_systems` | single | "STOP ALL IRRIGATION" button |

### Automations

| Automation | Trigger | Does |
|---|---|---|
| `schedule_reactor` | any of 21 schedules → on; HA start | starts the zone for remaining block time |
| `irrigation_refresh_weekly_pattern_on_schedule_edit` | any schedule change | forces the day-strip sensor to re-read (twice, 15 s apart) |
| `irrigation_schedule_grid_over_limit_verification` | day-strip sensor changes | persistent notification listing over-limit zone/days |
| `irrigation_over_20_gpm_warning` | over-limit binary sensor | persistent notification with live per-system breakdown |

### Events (consumed by the Action Log)

| Event | Log mark |
|---|---|
| `irrigation_zone_retried` | 🟡 started on the retry, runtime trimmed |
| `irrigation_zone_rejected` | 🔴 never started — interlock or controller offline |
| `irrigation_zone_skipped` | 🔴 skipped by the (removed) GPM gate — legacy |
| `irrigation_controller_run_started` | 🟢 Run Controller sequence began |
| `irrigation_controller_run_blocked` | 🔴 blocked by the gallon pre-flight |
| `irrigation_controller_run_empty` | ⚪ pressed with every zone disabled |
| `irrigation_stop_all_systems` | 🛑 global stop pressed |
| `irrigation_manual_press_ignored` | 🔵 manual press on a zone running from its schedule — ignored on purpose |
| `irrigation_single_run_over_gpm` | ⚠️ manual start pushed the pump over its limit — ran anyway |

Plus every zone's on→off transition, logged with its **actual** measured runtime — so the
log reflects what really happened, not what was configured. Sub-5-second transitions are
suppressed as firmware refusal flicks. Trigger-based template sensor, so the log survives
restarts.

### Helper naming pattern

Every helper follows `<domain>.outside_<sys>_zone_<N>_<role>`, where `<sys>` is
`irrigation` (East), `garden`, `west1`, or `west2`:

| Role | Example |
|---|---|
| per-zone schedule enable | `input_boolean.outside_west1_zone_3_enabled` |
| controller master | `input_boolean.outside_west1_schedule_enabled` |
| Run Now duration | `input_number.outside_west1_zone_3_duration` |
| GPM estimate | `input_number.outside_west1_zone_3_gpm` |
| countdown timer | `timer.outside_west1_zone_3_timer` |
| controller status text | `input_text.outside_west1_status` |
| ESPHome duration | `number.outside_irrigation_west1_controller_zone_3_duration` |

East is the exception: its `<sys>` is `irrigation`, not `east`
(`input_boolean.outside_irrigation_zone_3_enabled`) — it was the first controller built and
the bare name stuck. Do not rename it; every map, dashboard and automation references it.

---

## 10. How it got here — the stages

Each stage exists because the previous one broke in a specific way.

**Stage 1 — one 6-zone board, manual only.** East controller flashed and wired; all six
zones switchable from HA. Proved the relay/valve/firmware chain. The station map turned out
to be wrong (a "5-zone" controller was actually 6, with two names swapped), which is why
every later controller was re-surveyed terminal by terminal rather than trusted.

**Stage 2 — firmware owns shutoff.** Auto-off timers and the duration gate moved into the
ESP32 so an HA outage could not leave a valve open. HA stopped sending `switch.turn_off`
during normal runs.

**Stage 3 — three more controllers.** Garden, West 1, West 2. Same firmware shape, same
per-board max-2 interlock. 21 zones total.

**Stage 4 — controller-level scheduling. Abandoned.** Each controller had a "run them all"
session block. Changing one zone's seasonal runtime meant re-sizing the whole controller
block — one intent, many edits. Removed 2026-07-08.

**Stage 5 — per-zone scheduling (current).** One schedule helper per zone; the block *is*
the runtime. One generic reactor covers all 21 zones through maps, so onboarding a zone is
a few map lines, not a new automation.

**Stage 6 — area prefixes.** Every entity assigned to an area gained that area's name as an
entity_id prefix. Missing `outside_` prefixes caused extensive rework across the West 1 and
West 2 files.

**Stage 7 — GPM visibility.** Per-zone GPM helpers, per-system and total sensors, the
over-limit banner and the weekly pattern's blue/yellow dots.

**Stage 8 — interlock retry.** Motorised valves take >15 s to close, so a zone starting as
its predecessor closed was being bounced by the max-2 backstop and silently not watering.
One retry at 30 s with the runtime trimmed a minute.

**Stage 9 — runtime GPM blocking removed (2026-07-27).** Block-boundary race conditions made
the runtime gate skip legitimate zones. Enforcement moved to programming-time grid
verification. Nothing blocks a zone at runtime any more.

**Stage 10 — the action log.** A rolling history computed from actual switch timings, with
distinct marks for retried, rejected and skipped starts.

**Stage 11 — manual controller runs (2026-08-07).** The "Run Controller" button
with its gallon pre-flight, one global "STOP ALL IRRIGATION", master toggles moved onto
their own controller pages, per-zone tinted sections.

**Stage 12 — one implementation for all four controllers (2026-08-10, current).**
Until now each controller owned its own copy of run/stop/sequence — twelve scripts, four
implementations of the same logic. They had drifted, and the drift caused a real bug: West 1
and West 2 ended a manual run with an unconditional `switch.turn_off` after a plain `delay`,
where East and Garden did not. Because the script is `mode: parallel`, every "Run Now" press
armed an independent "close this valve in N minutes" that outlived its own run. Four presses
on West 2 Zone 1 produced this:

| | valve ON | valve OFF | closed by |
|---|---|---|---|
| press 1 | 15:24:13 | 15:24:21 | stopped by hand |
| press 2 | 15:28:11 | 15:39:13 | press 1's delay (15:24:13 + 15:00) |
| press 3 | 15:39:38 | 15:43:11 | press 2's delay (15:28:11 + 15:00) |
| press 4 | 15:43:54 | 15:54:38 | press 3's delay (15:39:38 + 15:00) |

A 15-minute run measured 10 min 44 s. The firmware, the duration slider and the action log
were all correct. Every zone script collapsed into `packages/irrigation_zone_engine.yaml`:
one registry of 21 zones and four generic scripts taking a controller key. The runner now
**waits** for the valve to close instead of scheduling a close, so a run that ends early
leaves nothing behind — and a controller can no longer drift from its siblings.

**Stage 13 — the firmware countdown became restartable (2026-08-17, current).** The
auto-off was an inline `delay:` inside `on_turn_on`, so the duration number was read **once**
at valve-open and inert thereafter. Re-arming a running zone changed the displayed number and
nothing else; the valve still closed on the original deadline. Worse, ESPHome de-duplicates
`publish_state`, so re-issuing `switch.turn_on` on an open valve never re-fired the trigger
either — the only way to re-read the arming was to close and reopen the valve.

The countdown moved into a per-zone `mode: restart` script driven by the duration number's
`on_value`. Writing a new duration to a running zone now restarts its countdown **in place**,
and `mode: restart` guarantees a zone can never hold two deadlines — which is what made the
inline delay unsafe to reset at all. All 21 zones moved to one shared `zone_timer.yaml`, and
every board publishes a `Config Revision` sensor so the build on the chip is checkable from
HA rather than inferred.

Verified on West 1 Zone 1: opened 21:36:50 armed for 15 min, re-armed to 3 min at 21:38:50,
closed 21:41:50 — exactly 3:00 later, with a single uninterrupted ON in the recorder.

**Stage 14 — manual/scheduled separation and single-run flow reporting (2026-08-17).** Once
a manual press could genuinely change a running zone, it also had to be prevented from
changing a *scheduled* one, so the guard in §5 Path B was added. At the same time manual
single-zone runs gained the GPM reporting they had never had (§7d).

**Stage 15 — HA stopped holding deadlines (2026-08-20, current).** Making the countdown
restartable reintroduced Stage 12's bug from the opposite direction. The zone runner still
carried a backstop — a `wait_template` whose timeout was `dur_min * 60 + 45`, closing the
valve if the firmware had not — and that timeout was computed from the duration the run
*started* with. A mid-run reset extended the firmware's countdown. The backstop's did not
move with it.

West 1 Zone 6, 2026-08-20: opened 15:45:07 armed for 21 min, re-armed to 31 min at 15:50:19,
closed 16:06:54 — which is 15:45:07 plus 21 min 45 s exactly, with the firmware's own
countdown still reading 867 s. HA then raised "zone did not auto-off", blaming a controller
that had done everything right. And because the runner is `mode: parallel`, the instance that
expired belonged to the *first* press while the run it closed belonged to the *second*: Stage
12's parallel-shutoff bug wearing a different hat.

The backstop was removed outright rather than repaired. `irrigation_run_zone` now holds no
deadline of any kind — it arms the zone, waits for the valve, and does its bookkeeping. A
firmware that fails to auto-off leaves its valve open until someone presses Stop. That gap is
accepted (§12) on the reasoning that any deadline HA holds is a deadline HA can act on
wrongly, and both times it held one, it did.

---

## 11. Improvements this design bought

- **One intent, one edit.** Seasonal runtime changes are a block resize.
- **A dead HA cannot flood the property.** Firmware ends every run.
- **A reboot never resurrects a stale run**, and never leaves a countdown lying about a
  valve that is already shut.
- **Silent failures were made loud.** A zone that does not start produces a system log
  error, a persistent notification and a red action-log entry naming the cause — interlock
  refusal or controller offline.
- **The log records what happened, not what was ordered** — runtimes come from the switch's
  own state timestamps, so manual toggles and stop-alls are captured too.
- **Onboarding a zone is additive**: a UI schedule helper, a registry row, five map lines,
  a GPM figure. There is one implementation to change, not four.
- **Over-limit programming is visible before it waters**, on the same weekly grid used to
  program it.

---

## 12. Limits and known gaps

### Considered and declined

**Renaming zone entities to stable short IDs** (`switch.…_zone_2` instead of
`switch.…_zone_2_south_center_b`), 2026-08-17. The argument for it is sound: an entity_id is
an identity and a zone description is presentation, so baking "South Center B" into the ID
means a later rename either makes the ID a lie or forces edits across 239 references. Generic
names would also have made the four firmware files fully identical, letting the switch block
join `zone_timer.yaml`.

Declined on readability — the descriptive IDs make the YAML and the logs legible at a glance,
and the change would orphan 21 entities (ESPHome derives `unique_id` from the name string, so
renaming creates new entities rather than renaming existing ones). Revisit only if zones are
actually renamed.

**Design limits — deliberate**

- **No weather awareness.** No rain delay, no ET adjustment, no forecast skip. Wanted early
  on; never built. Everything is calendar-driven.
- **Nothing blocks an over-limit *scheduled* run.** By decision. The grid warns; the pump
  tolerates brief overlap; only the manual Run Controller refuses.
- **Max 2 zones per board is per-board.** Four boards could open 8 zones between them —
  roughly 100+ GPM against a 20 GPM pump. Only the schedule grid prevents this.
- **GPM figures are estimates**, hand-entered, not metered. Every budget decision inherits
  their accuracy.
- **No flow or pressure sensing.** A stuck valve, burst line or dead pump is invisible; the
  system reports what it *commanded*, not what flowed.
- **Nothing catches a firmware that fails to auto-off.** If a controller opens a valve and
  never closes it, the valve stays open until someone presses Stop. HA is deliberately not
  watching for this (§6): the backstop that used to watch cost a healthy run (§10 Stage 15),
  because judging a controller overdue means judging its own countdown, and HA's copy of
  that countdown is always a little behind the chip's. If this is ever wanted, it belongs in
  `zone_timer.yaml` — the firmware declaring itself overdue, with HA relaying the verdict
  rather than reaching one.

**Known gaps — would be worth fixing**

- **East and Garden duration helpers carry `initial:`**, so a restart resets them (East
  30/30/30/30/30/15, Garden 4/5/40). West 1 and West 2 omit it and persist. Inconsistent;
  the East header documents its values as deliberate, so this was left alone rather than
  changed silently.
- **`sensor.irrigation_schedule_days` has no `outside_` prefix** and no area. Renaming it
  would break the reactor, both verification automations and every Weekly Pattern card, so
  it stays until there is a reason to do the full audit.
- **Many entities still have no area assigned** — see §13.
- **West 2 Zone 5 is still "Station 11"** — physical area never confirmed.
- **The West 1 firmware comment still says "GPM per station: not yet confirmed"**; all six
  are surveyed. Left as-is because editing an ESPHome file marks the device as needing a
  reflash for a comment change.
- **A `run_minutes` cap of 240** silently truncates any block longer than 4 hours.
- **Recorder keeps 5 days.** Long-term water-use history does not exist.

---

## 13. Common procedures

**Water a zone right now** — its page → zone card → **Run Now** (uses that zone's Run Now
Duration). Or tap the zone's dot on the zone map.

**Water a whole controller now** — its page → **Run Controller**. Runs every zone whose
"Schedule Enabled" is on, in numerical order. If it refuses, read the notification: it names
which zones are over the gallon budget.

**Change when a zone waters** — its page → Current Schedule → the zone row → ⚙️ → edit the
weekly grid. The block length is the runtime.

**Take a zone out of the rotation** — turn off its "Schedule Enabled". The schedule stays;
the zone is skipped by both scheduled runs and Run Controller.

**Disarm a whole controller** — its page → **Automation** button → OFF.

**Stop everything now** — Overview → **STOP ALL IRRIGATION**. Closes all 21 valves and kills
every sequence. Scheduling stays armed.

**Onboard a new zone**
1. Wire the valve to a free channel; add the switch + duration number to that board's
   ESPHome YAML following the existing pattern; flash.
2. Add the enable boolean, duration, timer to the controller's package.
3. Add its GPM helper to `irrigation_gpm_monitor.yaml` and include it in that system's
   sensor **twice** — the state template and the `zones` attribute template.
4. Create the schedule helper in the UI: plain name → save → reopen → area **Outside** →
   save → reopen → refresh entity ID.
5. Add it to `scheduling_core.yaml`: the reactor trigger list, `all_zone_schedules`, and all
   five maps.
6. Add it to `zone_registry` and `sys_zone_numbers` in `irrigation_zone_engine.yaml`, to the action log's trigger
   list and `labels`, to that controller's sequential runner, and to the dashboard.
7. Reload YAML and confirm the new entities appear.

**Deploying config** — files under `/config` are root-owned; `scp` and plain redirects fail:

```bash
ssh -i ~/.ssh/<your_key> <user>@<your-ha-host> "sudo tee /config/packages/<file>.yaml" < localfile
```

Then Developer Tools → YAML → Reload all YAML configuration, and verify the new entities
appear. `ha core check` does **not** work from the SSH addon (no supervisor token), so
validate locally before pushing.
