# Field report: RS-Oekoboiler 03 (300 L, SY384) – v2

Contributed by Oliver (updated 2026-11-04). This replaces my first report from 2026-11-01.
**Section 3 of that report ("self-heal") was wrong** – see "Water temperature" below.

## TL;DR
- **Water temperature can be decoded absolutely from byte 14 alone** (2-bit quadrant + 6-bit fine
  value). No wrap counter, no state, no manual correction needed. Validated against 16 display
  readings (±0.2 °C) and two predicted quadrant transitions.
- **Byte 15 is not a temperature band.** Using it to re-anchor the wrap counter caused ±6.3 °C jumps.
- **Four more temperatures are on the bus** (CE bytes 16, 18, 20, 22): air intake, evaporator coil,
  suction gas and hot gas (discharge), plus a "compressor running" bit (CE byte 27, bit 7).

## Setup
- **Unit:** RS-Oekoboiler 03, 300 L, main board SY384, display panel "Theodoor GT10.FAD.1-03-1".
  F90 on the display shows `384` / `34` / `9` / `81` (pressing ▼ repeatedly).
- **Connector:** 3-wire JST SM between display and main board: red = 5 V, black = GND, blue = data.
- **Tap:** blue → 10 kΩ → GPIO17 (XIAO ESP32-C6), GPIO17 → 20 kΩ → GND, common GND. RX only.
  ~355 valid frames/min, 0 CRC errors.
- ESPHome with the native API (no MQTT).

## Frame basics (unchanged)
UART 2400 8N1, 32-byte frames, header `64 41`, byte 5 = XOR mask for bytes 2–29,
CRC-16/Modbus over bytes 0–29. Two frame types: `d[11] = 0xCE` and `d[11] = 0xCF`.
All byte numbers below refer to the frame **after** XOR-unmasking.

## 1. Water temperature (CE byte 14) – absolute decoding

- bits 5..0 = `fine` (falls as temperature rises)
- bits 7..6 = quadrant `Q`. It counts **up** with rising temperature (2 → 3 → 0 → 1) and switches
  **in the middle** of a fine wrap, not at the wrap itself.

With `N = fine + 64·k`, choose `k` so that `N` is closest to the window centre for `Q`:

| Q | centre of N |
|---|---|
| 2 | 191.5 |
| 3 | 127.5 |
| 0 | 63.5 |
| 1 | −0.5 (hot end) – or 255.5 at the cold end |

```
T_raw     = 60.62 − 0.0986 · N
T_display = T_raw + F19          (F19 = +2.0 on my unit, factory default)
```

`Q` + `fine` determine `N` only up to multiples of 256 (= 25.2 °C). I keep the last value (raw)
in a restored global and pick the copy closest to it. That handles the Q=1 ambiguity and also
works above 65 °C:

```cpp
const float A = 60.62f, B = 0.0986f;
int fine = d[14] & 0x3F, q = (d[14] >> 6) & 3;
float c = (q == 2) ? 191.5f : (q == 3) ? 127.5f : (q == 0) ? 63.5f : -0.5f;
int n = fine + 64 * (int) lroundf((c - fine) / 64.0f);
float ref = id(wt_abs_ref);                       // float global, restore_value: yes, init -1000
if (ref > -100.0f) n += 256 * (int) lroundf(((A - ref) / B - n) / 256.0f);
float t = A - B * n;                              // raw; add F19 offset as a filter
if (ref < -100.0f || fabsf(t - ref) > 3.0f) id(wt_abs_ref) = t;   // rare flash writes
```

**Validation**
- 16 display readings between 45 and 64 °C: ±0.2 °C.
- Transition Q 2→3 observed at display **46.9 °C** (predicted 46.9).
- Transition Q 3→0 observed at display **53.2 °C** on 2026-11-04 (predicted 53.2), no jump.
- Transition Q 0→1 predicted at **59.5 °C** – not yet observed with this decoder; will report.

**Why the old approach jumped:** byte 15 is constant `0xCE` on this unit (it also appears in CF
frames), byte 13 is constant `0x26`. Treating byte 15 as a coarse band and re-anchoring the wrap
counter on every frame – plus the "self-heal" from my first report – produced jumps of exactly one
wrap (64 counts ≈ 6.3 °C), e.g. 48.8 ↔ 54.5 °C. Please don't use the self-heal.

**Stratification note:** the only water sensor sits at 965 mm in a 1900 mm tank. After a draw the
reading stays flat for up to ~1 h while the heat pump heats the cold bottom layer (compressor power
rises meanwhile), then climbs ~3.5–6 K/h once the tank mixes. That's physics, not the decoder.

## 2. Refrigerant circuit sensors (CE bytes 16, 18, 20, 22)

At standstill only these bytes (plus 14) change in CE frames. All behave like NTC readings with
roughly 0.1 K per count. Calibrated against the display on the main screen
(**▲ = intake air "Umgebung", ▼ = evaporator coil**) at standstill (18/18 °C) and during
operation (15/3 °C):

| Byte | Encoding | Count direction | Sensor (manual) | Formula (my unit) | Status |
|---|---|---|---|---|---|
| 18 | 3-bit quadrant + 5-bit fine, quadrant switches mid-wrap | up = colder | **Air intake** (A4, ▲) | `15.0 − 0.077·(M − 94)` | ✅ display-checked |
| 16 | plain counter, `v = d[16] ^ 0x01` | up = colder | **Evaporator coil** (A2, ▼) | `3.0 − 0.101·(v − 356)` | ✅ display-checked |
| 22 | plain counter, `v = d[22] ^ 0x13` | up = colder | **Suction gas** (A9, F79) | `18.0 − 0.095·(v − 62)` | 🟡 F79 check pending |
| 20 | same quadrant + fine scheme as water | up = **hotter** | **Hot gas / discharge** (A3) | `18.5 + 0.0986·(N + 86)` | 🟡 plausible, not on display |

All counters exceed 8 bits over their range, so I unwrap them by picking the copy (±256) closest
to the previous value. The offsets in the formulas depend on that unwrapping (my reference points:
counts at standstill ~18 °C), so treat them as *my* calibration, not universal constants.

Typical values in a run (water 47 → 54 °C, room ~16 °C): intake 15.4 °C, coil 2–3 °C (steady),
suction 3–6 °C (oscillates with the EEV, ~10 min period), superheat 0–4 K (F73 target 5 K),
hot gas 43 → 73 °C (rises with water temperature).

After the compressor stops, coil and suction rise to ~25 °C within 2 min: the EEV opens fully at
standstill (F70 reads 480 = maximum) and warm refrigerant equalises back into the evaporator.

## 3. Status bits found during operation

| Where | Idle | Running | Meaning |
|---|---|---|---|
| CE byte 27, bit 7 | `0x1C` | `0xBC` | **compressor running** (matches power meter exactly) |
| CE byte 21, bit 0 | `A1` | `A0` | probably fan |
| CE byte 17 | `0x90` | `0x6F` for a few seconds at start | start-up phase |
| CF byte 15, bit 0 | `CE` | `CF` | heating active |
| CE byte 10, bit 6 | | | PV contact (0 = closed), as in this repo |
| CF byte 10 & 0x1F | | | mode, see below |

- **CF byte 14** is a runtime counter: +1 every 30 s in a Gray-code-like order (not a temperature).
- Panel mode changes (AUTO / ECON / HEATER) and setting the clock did **not** show up in any byte I
  display. They may be in the bytes that look like noise (CE 4/24/26/28, CF 2) – not analysed yet.
- EEV position (F70) does not appear in the visible bytes either.

## 4. Mode codes (`CF[10] & 0x1F`)
- `0x10` heating (heat pump) ✔
- `0x19` "warm" = current internal setpoint reached ✔
- `0x1B` = HTR / electric heater: with F8 = 65 the heat pump stops at about 62.5–63 °C and the
  display shows "HTR" (heats on at ~4 K/h).
- `0x1D` defrost, and also for ~1 s right after the PV contact opens.

## 5. Settings and display facts (SY384)
- **F19** water sensor calibration, default +2 °C → explains a constant 2 °C offset between bus and display.
- **F62 = 1**: PV contact closed → setpoint F8, open → F9. Arrow keys can't change the setpoint then.
  The PV logic only applies in AUTO mode (not ECON/HEATER).
- **F78 (ambient) does not exist** on my display although the manual lists it; use ▲ on the main
  screen instead. F79 (suction gas) and F70 (EEV, 0–480) are readable.
- Datasheet says max. 55 °C with the heat pump; in practice it reaches ~62.5 °C before switching to
  HTR, with COP dropping from ~2.7 (below 55 °C) to ~1.6 (55–62 °C).

## 6. Usage example
- F9 = 46 (base), F8 = 65 (max), F17 = 0 (built-in legionella off), F12 = 5 (hysteresis).
- Home Assistant closes the PV contact once a day (11:00, legionella days 10:00) and opens it when
  the bus temperature reaches the day's target: 52 °C base, 54–62 °C in PV stages depending on
  battery SOC, PV power and grid export, 65 °C weekly on the sunniest day (forecast), at least every 14 days.

## Open points / help wanted
1. Confirm the Q 0→1 transition at 59.5 °C (next legionella run).
2. Confirm suction gas (byte 22) against F79 during a run.
3. If you have another SY384 unit: do bytes 16/18/20/22 behave the same? Same XOR keys (0x01, 0x13)?
   Please post raw CE frames together with ▲ / ▼ / F79 readings – ideally once at standstill and
   once ~20 min into a heating run.
