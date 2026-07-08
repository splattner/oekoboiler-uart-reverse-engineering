# Analysis scripts

Helpers used to reverse-engineer and verify the protocol. Pure standard-library
Python 3 (no dependencies) except where noted.

| Script | What it does |
|---|---|
| `decoder.py` | Reference implementation: framing, XOR demask, CRC, mode/PV, and **both** water-temperature decoders (stateless + continuity). Run directly for a self-test. This is the executable spec. |
| `correlate.py` | Ranks every decoded byte by correlation with the OCR ground-truth temperature over a time window. The method that located the temperature byte. Reads InfluxDB via env vars. |

## Using `correlate.py`

```bash
export INFLUX_URL=http://your-influx:8086
export INFLUX_USER=... INFLUX_PASS=...
export INFLUX_DB_BYTES=boiler-debug      # decoded bytes: measurement `data`, field `intValue`
export INFLUX_DB_OCR=homeassistant       # OCR temp: measurement `°C`, field `value`
export OCR_ENTITY=oekoboiler_water_temperature

python3 correlate.py "'2026-07-07T19:00:00Z'" 15m
```

Run it over a window that contains a real temperature **sweep with both heating and
cooling** — over pure monotonic cooling almost every byte correlates and the ranking is
meaningless. And remember the lag-scan caveat: confirm a candidate peaks at **lag 0**
before believing it (see the main write-up).

> `decoder.py` needs no InfluxDB — it works on raw 32-byte frames / `(ce14, ce15)` pairs.
