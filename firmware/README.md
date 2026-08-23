# Airgap firmware

`airgap/airgap.ino` is the Arduino UNO command interpreter for the Airgap
device. It uses the core-provided `Wire` library and the official `Servo`
library, which is installed separately. The optional LCD driver targets the
common PCF8574 backpack at I²C address `0x27`.

## Wiring

Disconnect USB and external power while wiring. All low-voltage modules must
share ground. Do not route mains voltage through a breadboard.

| UNO pin | Signal | Connection |
|---|---|---|
| `D2` | APPROVE button | Button between `D2` and GND; firmware enables the internal pull-up |
| `D3` | DENY button | Button between `D3` and GND; firmware enables the internal pull-up |
| `D4` | NEVER button | Button between `D4` and GND; firmware enables the internal pull-up |
| `D5` | Green LED | LED anode through 220 Ω to `D5`; cathode to GND |
| `D6` | Red LED | LED anode through 220 Ω to `D6`; cathode to GND |
| `D7` | Relay input | Active-HIGH relay module `IN`; module VCC to 5 V and GND to GND |
| `D8` | Piezo | Piezo positive lead to `D8`, negative lead to GND |
| `D9` | Flag servo | SG90 signal; use a suitable 5 V supply and connect its ground to UNO ground |
| `A0` | Autonomy dial | 10 kΩ potentiometer wiper; outer legs to 5 V and GND |
| `A4` | LCD SDA | Optional 16×2 PCF8574 LCD SDA (`0x27`) |
| `A5` | LCD SCL | Optional 16×2 PCF8574 LCD SCL (`0x27`) |

The relay is active HIGH: LOW or an unpowered/floating input is open. Use the
module's normally-open contact for the controlled circuit. This is an
enforcement boundary only when that contact is physically in the controlled
thing's power or signal path; for ordinary software actions the device is a
consent channel, not a physical barrier.

## Compile and flash

Install [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/),
then install the AVR core and Servo library once:

```text
arduino-cli core update-index
arduino-cli core install arduino:avr
arduino-cli lib install Servo
```

From the repository root, compile for an UNO:

```text
arduino-cli compile --fqbn arduino:avr:uno firmware/airgap
```

Connect the board, identify its port with `arduino-cli board list`, and upload
(replace `COM3` with the listed port):

```text
arduino-cli upload --port COM3 --fqbn arduino:avr:uno firmware/airgap
```

The protocol runs at 115200 baud. A reset must immediately produce a `boot`
event, leave the relay open, raise the flag, and light the red LED. Physical
bring-up and relay polarity checks belong to AIR-5.
