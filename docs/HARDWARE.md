# Hardware

Four identical sprinkler controllers.

---

## Sprinkler controllers ×4

**Waveshare Industrial 6-Ch ESP32-S3 Wi-Fi Relay Module**

| | |
|---|---|
| MCU | ESP32-S3-WROOM-1U-N16 (Wi-Fi + BLE), 16 MB flash |
| Relays | 6 channels, 10 A @ 250 VAC / 30 VDC, optocoupler-isolated |
| Board power | USB-C 5 V (a phone-charger-class supply is plenty) |
| Alt. board power | 7–36 V DC screw terminal — unused here |
| Extras | RS485, 40-pin Pico HAT header, rail-mount ABS case |
| Framework | ESP-IDF, via ESPHome |

One module per controller location. The locations are 50+ ft apart and cannot be
consolidated onto one wiring point, which is why there are four boards rather than one
large one.

### Relay → GPIO map

Identical on all four boards. The 3-zone controller populates only channels 1–3.

| Relay channel | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| **GPIO** | 1 | 2 | 41 | 42 | 45 | 46 |

GPIO45 and GPIO46 are ESP32-S3 strapping pins. They are safe as relay outputs here — the
relay inputs do not pull them during boot — but ESPHome warns about them, so both carry:

```yaml
pin:
  number: GPIO45
  ignore_strapping_warning: true
```

All relays are `restore_mode: ALWAYS_OFF`. Power loss closes every valve.

### Valve wiring

Standard residential sprinkler solenoids, **24 VAC**, driven through the relay contacts.
The relay switches the hot leg; COM is shared.

```
24VAC transformer ──┬── relay COM (jumpered across all used channels)
                    │
                    └── valve solenoid COM (shared return)

relay CH n NO ───────── valve n hot leg
```

- The **existing transformer from the old timer is reused**. On the first controller that
  was a 300 mA (~7.2 VA) unit — enough for the two-simultaneous-zone limit the firmware
  enforces, and a real reason that limit exists.
- The transformer powers **only the switched side**. The ESP32 board is powered separately
  over USB-C. Nothing on the board sees 24 VAC.
- Old timers were removed entirely; the existing valve wires land directly on the Waveshare
  screw terminals.

**Before wiring any controller**, photograph its terminal block first — labels, wire
colours, COM wiring and transformer rating. On this property the original station map was
wrong on two of the four locations (a "5-zone" controller was actually 6, with two zone
names swapped, and an "11-zone" location turned out to need two boards). Survey each
location terminal by terminal rather than trusting the old labelling.

### Per-board zone counts

| Controller | Zones | Notes |
|---|---|---|
| East | 6 | first board built |
| Garden | 3 | drip + raised beds; channels 4–6 spare |
| West 1 | 6 | old stations 1–6, 1:1 terminal mapping |
| West 2 | 6 | old stations 7–12 |

---

## The pump

One well pump, **~20 GPM**, shared by all 21 sprinkler zones. It is the binding constraint
on the entire design:

- Most individual zones draw **16 GPM** — so in practice only one zone fits the budget at a
  time, and any two rotor zones together exceed it.
- The firmware's max-2-zones-per-board interlock is a **local backstop**, not property-wide
  protection. Four boards could open eight zones between them; only the schedule grid
  prevents that.
- GPM figures per zone are **hand-entered estimates**, stored as `input_number` helpers so
  they can be corrected from the dashboard as real measurements arrive. There is no flow
  meter anywhere in the system.

Zone GPM on this property, for reference:

| | Z1 | Z2 | Z3 | Z4 | Z5 | Z6 |
|---|---|---|---|---|---|---|
| East | 16 | 16 | 16 | 16 | 15 | 5 |
| Garden | 3 | 10 | 10 | — | — | — |
| West 1 | 16 | 16 | 10 | 16 | 10 | 16 |
| West 2 | 16 | 16 | 16 | 16 | 16 | 12 |

Rotor zones are typically 4 half or full circle heads at 3–4 GPM per head. Drip and shrub-head
zones are much lower.
