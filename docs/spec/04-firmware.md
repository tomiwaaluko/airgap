# Firmware — FROZEN CONTRACT v1

One sketch. Flashed once. Never reflashed during a demo. It implements the
device side of [`01-serial-protocol.md`](01-serial-protocol.md) and nothing else.

## Pin map

This map is chosen to avoid AVR timer collisions, which is the single most
common way an UNO project breaks in a way that looks like a software bug. Do not
move pins without re-checking the timer table below.

| Pin | Signal | Mode | Notes |
|---|---|---|---|
| `D2` | `BTN_APPROVE` | `INPUT_PULLUP` | INT0-capable, active LOW |
| `D3` | `BTN_DENY` | `INPUT_PULLUP` | INT1-capable, active LOW |
| `D4` | `BTN_NEVER` | `INPUT_PULLUP` | active LOW |
| `D5` | `LED_GREEN` | `OUTPUT` | digital only, series 220Ω |
| `D6` | `LED_RED` | `OUTPUT` | digital only, series 220Ω |
| `D7` | `RELAY_IN` | `OUTPUT` | **active HIGH = closed.** LOW / floating = open = safe |
| `D8` | `PIEZO` | `OUTPUT` | driven by `tone()` |
| `D9` | `SERVO_FLAG` | Servo | SG90 signal |
| `A0` | `DIAL` | `INPUT` | 10kΩ pot, wiper to A0 |
| `A4` / `A5` | `SDA` / `SCL` | I²C | optional 16×2 LCD |

**Amber is `LED_RED` and `LED_GREEN` both HIGH.** There is no PWM RGB LED in this
design, deliberately — see below.

### Timer allocation (why the map looks like this)

| Timer | Owned by | Pins it would otherwise PWM |
|---|---|---|
| Timer0 | `millis()`, `delay()` | D5, D6 |
| Timer1 | `Servo` library | D9, D10 |
| Timer2 | `tone()` | D3, D11 |

Every hardware PWM pin on the UNO is claimed by something we need. That is why
the status LED is driven **digitally** with two discrete channels instead of as a
PWM RGB LED: a PWM RGB LED on D3/D5/D6 would fight `tone()` on Timer2 and the
red channel would glitch every time the buzzer sounded. Two digital pins give
the three states the protocol actually defines (`green`, `amber`, `red`) with no
timer contention at all.

## Boot sequence

1. `RELAY_IN` driven LOW **first**, before anything else.
2. Servo attached, flag driven to the **up** (blocked) position.
3. `LED_RED` HIGH.
4. Serial begins at 115200.
5. Emit `{"ev":"boot","fw":"1.0.0","t":<millis>}`.
6. Enter `DISARMED`.

The device always comes up refusing. A power cycle mid-demo is a denial, not an
approval.

## State machine

```
DISARMED  --arm(req)-->  ARMED  --btn approve--> APPROVED --(host relay cmd)--> ...
    ^                      |                         |
    |                      +--btn deny/never--> DENIED
    |                                                |
    +----------------- disarm ------------------------+
```

- In `DISARMED`, button presses still emit events but with `"req":null`.
- In `ARMED`, button events carry the armed `req`.
- The device **never** drives the relay itself. It reports; the host commands.
  This keeps the interlock in one place (the Supervisor) rather than two.

## Loop obligations

The main loop must complete in **under 5 ms** in all cases. Consequences:

- No `delay()` anywhere. Use `millis()` deltas.
- `tone()` is non-blocking; never follow it with `delay()`.
- Serial reads are non-blocking, one line assembled per pass into a 200-byte
  buffer. Overflow discards the line and counts it.
- Button debounce is 25 ms, implemented with `millis()`, not `delay()`.
- The dial is read every loop and low-pass filtered; `tick` reports the filtered
  value mapped `0..1023 -> 0..10` with hysteresis of ±1 raw step to stop it
  flickering between levels.

## Bring-up checklist (ticket AIR-5, human task)

Do these in order on real hardware before trusting any of it:

1. `ping` round-trips and acks in under 100 ms.
2. Each of the three buttons emits exactly one event per press (debounce works).
3. The dial reports 0 at one extreme and 10 at the other, monotonically.
4. Servo reaches both flag positions without buzzing at rest (detach if it hums).
5. Relay clicks on `relay(closed=true)` and the load actually switches.
6. **Unplug the USB cable while the relay is closed.** The relay must open.
   If it does not, the wiring is inverted and everything downstream is unsafe.
7. `tone()` sounds while the LED is red — confirm no visible LED glitch.
