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
never change. Where CF carries data (`cf[14]` is a timer), the key had to be recovered
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

### Temperatures — five `int16` channels (CE frames)

Every temperature sensor is a **signed 16-bit little-endian integer in 0.1 °C**, once the
fixed key above is removed:

```
T (°C) = int16( (plain[i+1] << 8) | plain[i] ) / 10        plain[j] = dec[j] ^ KEY[j]
```

| low/high byte | sensor | heating → idle (2026-11-12) | name confidence |
|---|---|---|---|
| `dec[14/15]` | **water / tank** | 48.9 → 49.6 °C | ✅ checked against the display (OCR), 46–60 °C |
| `dec[16/17]` | return gas (suction) | 1.6 → ~15 °C | provisional |
| `dec[18/19]` | ambient / intake air | 13.5 → ~19.5 °C | provisional |
| `dec[20/21]` | exhaust / discharge | 54.8 → decays after stop | provisional |
| `dec[22/23]` | coil / evaporator | 1.8 → ~21 °C, spike at the end-of-cycle "defrost" | provisional |

The SY-384 schematic lists five channels (water, coil, ambient, exhaust, return gas), and
all five carry live data. The names for 16/18/20/22 come from how each channel behaves
when the compressor stops, not from board labels, so 16 and 22 in particular could be
swapped.

Stateless, no calibration constants, valid over the full range. The value is assumed
signed (sub-zero coil readings in winter should then decode directly — not yet observed).

> **Superseded model (July 2026).** Before the second key was found, the tank was decoded
> as "`dec[14] mod 64` falls ~10 units/°C and wraps every 6.4 °C; `dec[15]` = 206/205
> selects the band". That was the int16 seen through the key: `dec[15]` 206/205 is high
> byte 1/2 (the band edge is exactly 51.2 °C), and `0x1D` flips low-byte bits 0, 2, 3, 4,
> which looks like a noisy falling sawtooth. The old fit was only usable over 46–60 °C,
> and readings below ~46.1 °C folded up to ~52 °C.

## Other fields

`dec[24/26/28]` alternate between two values every few frames (protocol heartbeat, not
sensor data). XOR-ed with the matching CF byte they read `127/128` and `191/192`.
`cf[14]` is a heating-phase timer (clear text `cf[14] ^ 0x1D`; parks at `0xFD` → `0xE0` when idle).
`cf[15] = 0x30` appears in a short burst with `cf[10] = 0x5D` at the end of each heating cycle.
