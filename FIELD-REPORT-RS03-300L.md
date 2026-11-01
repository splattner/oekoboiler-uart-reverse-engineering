# Field report: RS-Oekoboiler 03 (300 L, SY384)

Contributed by Oliver (2026-11-01). Findings from running this decoder on a second unit.

## Setup
- **Unit:** RS-Oekoboiler 03, 300 L, main board SY384, display panel "Theodoor GT10.FAD.1-03-1"
- **Connector:** 3-wire JST SM between display and main board: red = 5 V, black = GND, blue = data
- **Data level:** 5 V (I measured an average of 2.2–2.8 V with a multimeter while frames were flowing)
- **Tap:** blue → 10 kΩ → GPIO17 (XIAO ESP32-C6), GPIO17 → 20 kΩ → GND, common GND. RX only.
  Result: ~355 valid frames/min, 0 CRC errors. So the bus carries about 6 frames/s in total
  (both directions), not 3/s.
- ESPHome **without MQTT** (native API only). The decoder was ported 1:1, plus frames/min and
  CRC-errors/min counters as diagnostics.

## 1. Offset = F19 (water sensor calibration, factory default +2 °C)
On this unit the decoded temperature was consistently **2.0 °C below the display**
(display flips 50→51, 51→52 and 52→53 matched decoded +2.0 within 0.0–0.3 °C).
The SY384 parameter table lists **F19 "Wassertemperaturfühler Kalibrierung", range −5…5, default 2**.
So the bus seems to carry the raw sensor value, and the display adds F19.
This might explain constant offsets on other units.

## 2. Valid above 60 °C
Heated up to 65 °C (legionella run). The formula `T = 60.62 − 0.0986 × (fine + acc)`
stayed smooth with no discontinuities all the way from 48 to 64.3 °C.
Display check at 60: display 60, decoded +2 = 60.3.

## 3. Continuity decoder slipped one wrap once
At about 57 °C (during a shower plus circulation pump use) the continuity decoder suddenly
dropped by about 6.4 °C to ~51 °C, while the stateless decoder stayed correct (57.6).
A simple self-heal fixed it: if `|tA − tB| > 3 °C`, re-seed `wt_acc = 64 * kB`.

```cpp
if (fabsf(tA - tB) > 3.0f) {
  ESP_LOGW("oekoboiler", "wrap slip corrected: %.1f -> %.1f", tA, tB);
  id(wt_acc) = 64 * kB;
  tA = A - B * (fine + id(wt_acc));
}
```

## 4. Mode codes (`cf[10] & 0x1F`)
- `0x10` heating (heat pump) ✔
- `0x19` "warm" = the **current internal setpoint** is reached (F8 or F9, see below) ✔
- **`0x1B` = HTR / electric heater** (new): with F8 = 65, the heat pump stops at about
  62.5–63 °C and the display shows "HTR". It heated 62.5 → 64.5 °C at about 4 K/h.
- `0x1D`: besides defrost, it also shows up for ~1 s right **after the PV contact opens**
  (transition to idle).

## 5. Setpoint with F62 = 1 (from the SY384 manual)
- PV contact **closed** → setpoint = **F8** (factory 60)
- PV contact **open** → setpoint = **F9** (factory 50)
- F11 is ignored, and the arrow keys can't change the setpoint anymore.

This matches the 60/50 derived in this repo. The "setpoint" sensor is effectively F8/F9.

## 6. Usage example
- F9 = 46 (base), F8 = 65 (max), F17 = 0 (built-in legionella off), F12 = 5 (hysteresis)
- Home Assistant closes the PV contact once a day at 11:00 and **opens it as soon as the
  bus temperature reaches the day's target**: 54 °C base, 58 °C with enough PV / battery,
  65 °C weekly on the sunniest day (forecast-based), at least every 14 days.
- Measured heating rates (300 L, ~780 W heat pump): ~6 K/h up to 55 °C, ~3.5 K/h from
  55 to 62.5 °C, then ~4 K/h on the heater.
- ESPHome tip: `force_update: true` on the temperature sensor, so HA gets a heartbeat
  even when the value doesn't change (useful for a "bus alive" check).
