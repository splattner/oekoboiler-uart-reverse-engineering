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

### Second layer: fixed per-position key

On top of the per-frame `raw[5]` mask, the payload carries a **second XOR key that is
fixed per byte position** (identical in every frame, both families). CF frames are
mostly zero in clear text, so they transmit most of this key verbatim: `cf[16..23]`
never change. Where CF carries data (`cf[14]` is the expansion-valve position), the key had to be recovered
from the CE traffic itself (see [`scripts/keyscan.py`](scripts/keyscan.py)).

| pos | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
|---|---|---|---|---|---|---|---|---|---|---|
| key | `1D` | `CF` | `7E` | `90` | `0F` | `24` | `60` | `A1` | `EC` | `5E` |

```
plain[i] = dec[i] ^ KEY[i]
```

Bytes outside 14–23 are not mapped yet. `dec[12/13]` read `D9 26` in both families, which
looks like key over a zero payload.

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
| `0xCE` | **CE** | main board → panel: PV input flag, **all temperature sensors** |
| `0xCF` | **CF** | panel → main board: operating mode, expansion-valve position |

**Direction (inferred, 2026-11-12).** The two families arrive 1:1, so they are the two halves of
a request/response exchange. The panel has no sensors (3 wires only), so the sensor-carrying CE
frames must come from the main board, and CF from the panel. The PV flag in CE matches the PV
dry contact wired to the main board's terminals.

This makes the **panel the controller** and the main board an I/O board:
- CF carries nothing but the mode (heat / warm / defrost) and the EEV position;
  every other CF payload byte is constant.
- The **setpoint never crosses the bus** in either direction, so only the panel can decide when
  the tank is hot enough.
- The valve moves 1 step per 30 s in response to the superheat computed from CE sensors.
  The superheat loop, and most likely the defrost decision, therefore run in the panel.

Caveat: the sniffer only accepts CE/CF frames, so a rare settings frame (e.g. sent only on a menu
change) would go unnoticed.

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

### Temperatures — five `int16` channels (CE frames)

Every temperature sensor is a **signed 16-bit little-endian integer in 0.1 °C**, once the
fixed key above is removed:

```
T (°C) = int16( (plain[i+1] << 8) | plain[i] ) / 10        plain[j] = dec[j] ^ KEY[j]
```

| low/high byte | sensor | heating → idle (2026-11-12) | name confidence |
|---|---|---|---|
| `dec[14/15]` | **water / tank** | 48.9 → 49.6 °C | ✅ checked against the display (OCR), 46–60 °C |
| `dec[16/17]` | coil / evaporator | 1.6 → ~15 °C; drops first and lowest at compressor start | from superheat behaviour |
| `dec[18/19]` | ambient / intake air | 13.5 → ~19.5 °C | provisional |
| `dec[20/21]` | exhaust / discharge | 54.8 → decays after stop | provisional |
| `dec[22/23]` | return gas (suction) | 1.8 → ~21 °C; lags the coil, a little warmer while running | from superheat behaviour |

The SY-384 schematic lists five channels (water, coil, ambient, exhaust, return gas), and
all five carry live data. The names come from behaviour, not board labels. Coil vs return
gas is set by superheat (return gas − coil): with 16 = coil and 22 = return gas, the
expansion valve (below) opens when superheat is high and closes when it is ~0, which is
what a superheat controller does. The other way round, superheat would be negative.

Stateless, no calibration constants, valid over the full range. The value is assumed
signed (sub-zero coil readings in winter should then decode directly — not yet observed).

> **Superseded model (July 2026).** Before the second key was found, the tank was decoded
> as "`dec[14] mod 64` falls ~10 units/°C and wraps every 6.4 °C; `dec[15]` = 206/205
> selects the band". That was the int16 seen through the key: `dec[15]` 206/205 is high
> byte 1/2 (the band edge is exactly 51.2 °C), and `0x1D` flips low-byte bits 0, 2, 3, 4,
> which looks like a noisy falling sawtooth. The old fit was only usable over 46–60 °C,
> and readings below ~46.1 °C folded up to ~52 °C.

## Other fields

### Expansion valve position — `dec[14/15]` (CF frames)

Same int16 layout and key as the tank (`1D` / `CF`), but in CF frames, and in **motor
steps** rather than °C:

| phase | value |
|---|---|
| idle | `480` (parked open, so pressures equalise) |
| ~1 min after heating starts | jumps to `280` (start position) |
| heating | ±1 step per 30 s: opens while superheat is high, closes while it is ~0 |
| start of the end-of-run defrost (`cf[10] = 0x5D`, `cf[15] = 0x30`) | `-1` for ~2 s, then back to `480` |

**Superheat control, observed 2026-11-12** (superheat = return gas `22` − coil `16`):
- From 280 the valve closed one step per 30 s for ~90 min while superheat sat at 0.1–0.2 K
  (flooded evaporator), down to ~100 steps.
- From there it regulates with a dead band: **close** below ~3.5 K, **hold** between ~3.5 and
  6.5 K, **open** above ~6.5 K. Target ≈ 5 K; the valve settled at 85–92 steps.

### End of a heating run

Seen on both runs on 2026-11-12, at setpoint 50 and at 60 °C:
1. Heating stops when the tank reaches **setpoint − 0.5 °C** (49.5 / 59.5), i.e. when the
   whole-degree display would round up to the setpoint.
2. `cf[10]` goes to `0x5D` (**defrost**, the panel shows it). The compressor stops at once
   (power 735 → ~15 W). The valve reads `-1` briefly, then parks at 480. The coil warms
   from 0 to 8 °C within a minute as the pressures equalise.
3. After exactly **60 s**, `cf[10]` goes to `0x59` (warm/idle).

This short passive defrost at the end of a run is different from the 4–5 min mid-run defrosts.
Those keep the compressor running (power rises to ~700–780 W).

### Spare channels — `dec[24/25]`, `dec[26/27]`, `dec[28/29]` (CE frames)

Keyed with the matching CF bytes (`56 E3`, `71 BC`, `3F 58`), these are three more int16
slots that read a constant −12.8/−12.9 and −6.4/−6.5 with a one-count flicker: most likely
inputs with nothing connected. (Earlier notes called them a "heartbeat".)

### Not understood yet

- `raw[5]`, the per-frame mask, is pseudo-random, not a counter.
- `dec[4]` (CE) and `dec[2]` (CF) carry the same field at different offsets: `value ^ 0x5A`
  is always `2^k − 1`, with `k = 1` half the time, `k = 2` a quarter, and so on.
