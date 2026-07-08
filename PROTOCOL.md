# Ökoboiler Internal UART Protocol Reference

Board-to-board bus between the display/operation panel and the main control board
(**SY-384**, ATmega16) of an Ökoboiler heat-pump water heater.

> Reverse-engineered from capture; unofficial; may vary by firmware/model revision.
> Everything here was validated on one unit over several days of traffic.

## Physical layer

| Property | Value |
|---|---|
| Signalling | Single-wire, half-duplex UART, optically isolated (PC817) |
| Baud | 2400 |
| Framing | 8 data bits, no parity, 1 stop bit (8N1) |
| Panel connection | UART data + 5 V + GND only (panel carries no sensors) |
| Frame rate | ~3 frames/s per direction, continuous |

## Frame format

Fixed length: **32 bytes**.

```
offset  0   1   2 .......................... 29   30   31
       64  41  [ ---- payload (XOR-masked) ---- ]  CRC_lo CRC_hi
```

### Header
`raw[0] = 0x64`, `raw[1] = 0x41`. Use this to find frame boundaries in the byte stream.

### XOR masking
`raw[5]` is a per-frame XOR key. Decode the payload with:

```
dec[i] = raw[i] ^ raw[5]      for i = 2 .. 29
dec[i] = raw[i]               for i = 0,1,30,31   (header + CRC left as-is)
```

`raw[5]` itself decodes to `dec[5] = 0`.

### Signature (decode sanity check)
After demasking, `dec[6..9]` is the constant **`F2 D4 F9 61`**. If you don't see this,
your framing or XOR is wrong. (Zero variance across the entire capture.)

### CRC
`raw[30..31]` = **CRC-16/Modbus** over `raw[0..29]`, stored little-endian.

```
poly = 0xA001 (reflected), init = 0xFFFF
crc_frame = raw[30] | (raw[31] << 8)
```

Only act on frames whose CRC validates — a corrupted frame that happens to keep the
`64 41` header will otherwise poison your decoded values.

## Frame families / direction

`dec[11]` selects the frame family:

| `dec[11]` | Called here | Carries |
|---|---|---|
| `0xCE` | **CE** | PV mode, **water temperature** |
| `0xCF` | **CF** | operating mode |

(Which physical board originates which family is unresolved and irrelevant to decoding.)

## Decoded fields

### Operating mode — `dec[10] & 0x1F` (CF frames)
| Value | Mode |
|---|---|
| `0x10` | heating |
| `0x19` | idle / warm |
| `0x1D` | defrost |

### PV (photovoltaic/solar) mode — `dec[10] & 0x40` (CE frames)
| Bit | Meaning |
|---|---|
| clear (0) | PV mode ON (running on surplus solar; higher setpoint) |
| set (1) | PV mode OFF |

`dec[10]` is thus typically `0x19` (PV on) or `0x59` (PV off) in idle.

### Water / tank temperature — `dec[14]` + `dec[15]` (CE frames)

The displayed tank temperature is split across two bytes:

- **`dec[14]` — fine value.** Falls ~**10.14 units per °C** (`0.0986 °C/unit`), and
  **wraps every ~6.4 °C** (i.e. the temperature-bearing part is `dec[14] mod 64`).
  Byte value *decreases* as temperature *increases*.
- **`dec[15]` — coarse band.** Disambiguates the wrap:

  | `dec[15]` | Temperature band* |
  |---|---|
  | `206` | ~47–52 °C |
  | `205` | ~51–60 °C |

  \*Only these two values were observed in the 47–60 °C capture range; wider swings
  will reveal more.

**Decode:**
```
T (°C) = 60.62 − 0.0986 × unwrapped(dec[14])
```
where `unwrapped()` resolves the wrap either by continuity tracking or, statelessly,
by choosing the wrap count that lands `T` in the `dec[15]` band. See
[`scripts/decoder.py`](scripts/decoder.py) and the ESPHome lambda for both methods.
Constants calibrated over 47–60 °C; ~0.3 °C mean error vs OCR ground truth.

## The other sensor channels

All five temperature sensors use the **same `(fine, coarse)` byte-pair format** as the tank
(a "fine" byte that varies a lot and wraps, plus a companion "coarse" byte with only 2–4
values). The pairs are `dec[14/15]`, `dec[16/17]`, `dec[18/19]`, `dec[20/21]`, `dec[22/23]`.

| pair | sensor | identified by | absolute calibration |
|---|---|---|---|
| `dec[14/15]` | water / tank | display (OCR) | ✅ ~0.3 °C |
| `dec[16/17]` | ambient / intake air | correlation with a same-room temp sensor (r≈−0.82 idle) | offset anchored; slope approx |
| `dec[20/21]` | evaporator / coil | plunges cold during `heating` mode | shape ✅, magnitude approx |
| `dec[22/23]` | exhaust / discharge gas | rises hot during `heating` mode | shape ✅, magnitude approx |
| `dec[18/19]` | return gas | (elimination) | **unpopulated** on this unit — dead channel |

None of these four is shown on the display, so they were identified behaviourally rather
than against a display readout. Absolute magnitudes assume the tank's slope (~10 units/°C)
and are therefore only approximate — each channel has its own scale that requires per-channel
ground truth (a clamp-on pipe probe for coil/exhaust; a wider room-temperature swing for
ambient). The SY-384 schematic lists all five channels; the **return gas sensor is not
fitted**, so only **four sensors are physically present**.

`dec[24/26/28]` alternate between two values every few frames (protocol heartbeat, not
sensor data).
