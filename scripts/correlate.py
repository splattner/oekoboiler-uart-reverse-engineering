#!/usr/bin/env python3
"""
OCR<->byte correlation tool — the method that located the water-temperature byte.

Pulls the OCR ground-truth temperature and every decoded byte from InfluxDB, then
ranks each byte by |Pearson r| vs temperature over a time window. Run it over a
temperature *sweep* that includes both heating and cooling (see the write-up on why).

Config via environment variables (nothing hardcoded):
    INFLUX_URL        e.g. http://host:8086
    INFLUX_USER, INFLUX_PASS
    INFLUX_DB_BYTES   db holding decoded bytes   (measurement `data`, field `intValue`,
                      tags `byte` 0..29 and `group` ce/cf)
    INFLUX_DB_OCR     db holding OCR temperature (measurement `°C`, field `value`,
                      tag entity_id=OCR_ENTITY)
    OCR_ENTITY        default: oekoboiler_water_temperature

Usage:
    python3 correlate.py "<since-influx-time>" [bucket]
    python3 correlate.py "'2026-07-07T19:00:00Z'" 15m
"""
import os, sys, math, json, base64, urllib.request, urllib.parse

URL   = os.environ.get("INFLUX_URL", "http://localhost:8086")
USER  = os.environ.get("INFLUX_USER", "")
PASS  = os.environ.get("INFLUX_PASS", "")
DBB   = os.environ.get("INFLUX_DB_BYTES", "boiler-debug")
DBO   = os.environ.get("INFLUX_DB_OCR", "homeassistant")
ENT   = os.environ.get("OCR_ENTITY", "oekoboiler_water_temperature")


def q(db, query):
    u = URL + "/query?" + urllib.parse.urlencode({"db": db, "epoch": "ms", "q": query})
    req = urllib.request.Request(u)
    if USER:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode())
    return json.load(urllib.request.urlopen(req, timeout=120))


def buckets(db, sel):
    r = q(db, sel)
    try:
        return {v[0]: v[1] for v in r["results"][0]["series"][0]["values"] if v[1] is not None}
    except (KeyError, IndexError):
        return {}


def pearson(xs, ys):
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return (0.0, n, 0.0, my)
    slope = sxy / sxx
    return (sxy / math.sqrt(sxx * syy), n, slope, my - slope * mx)


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "now()-24h"
    bk = sys.argv[2] if len(sys.argv) > 2 else "10m"

    ocr = buckets(DBO,
        f'SELECT mean(value) FROM "°C" WHERE entity_id=\'{ENT}\' '
        f'AND time>{since} AND value>20 AND value<75 GROUP BY time({bk}) fill(none)')
    if not ocr:
        print("no OCR data in window"); return
    tv = list(ocr.values())
    print(f"OCR buckets={len(ocr)}  temp {min(tv):.0f}..{max(tv):.0f} °C  (sweep={max(tv)-min(tv):.0f})")

    res = []
    for grp in ("ce", "cf"):
        for b in range(30):
            by = buckets(DBB,
                f'SELECT mean(intValue) FROM data WHERE byte=\'{b}\' AND "group"=\'{grp}\' '
                f'AND time>{since} GROUP BY time({bk}) fill(none)')
            common = [t for t in ocr if t in by]
            s = pearson([by[t] for t in common], [ocr[t] for t in common])
            if s:
                res.append((abs(s[0]), s[0], grp, b, s[1], s[2], s[3]))
    res.sort(reverse=True)
    print("\n|r|    r      byte     n    temp = slope*byte + intercept")
    for ar, r, grp, b, n, sl, ic in res[:15]:
        print(f"{ar:.3f} {r:+.3f}  {grp}[{b:2d}]  {n:4d}   {sl:+.3f}*b {ic:+.1f}")
    print("\nTip: a real readout also peaks at lag 0 in a time-lag scan — a high |r| here "
          "alone can be a daily-cycle coincidence (see the write-up).")


if __name__ == "__main__":
    main()
