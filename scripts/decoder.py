#!/usr/bin/env python3
"""
Reference decoder for the Ökoboiler internal UART protocol.

Pure-Python implementation of everything the ESPHome lambda does:
  - frame framing + XOR demasking + CRC-16/Modbus check
  - operating mode, PV mode
  - water temperature (both the continuity and the stateless decoder)

Use it to sanity-check captures, or as the spec for a port to another platform.
See PROTOCOL.md for the field reference.
"""
from __future__ import annotations
from dataclasses import dataclass

# --- calibration (fitted over 47-60 °C) ---
WT_A = 60.62     # T = WT_A - WT_B * (fine + acc)
WT_B = 0.0986    # °C per fine unit  (~10.14 units/°C)


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc


def demask(frame: bytes) -> bytes:
    """XOR-decode payload bytes 2..29 with the per-frame key raw[5]."""
    mask = frame[5]
    out = bytearray(frame)
    for i in range(2, 30):
        out[i] = frame[i] ^ mask
    return bytes(out)


@dataclass
class Frame:
    raw: bytes
    dec: bytes
    crc_ok: bool

    @property
    def family(self) -> int:      # 0xCE or 0xCF
        return self.dec[11]

    @property
    def is_ce(self) -> bool:
        return self.dec[11] == 0xCE

    @property
    def signature_ok(self) -> bool:
        return self.dec[6:10] == bytes((0xF2, 0xD4, 0xF9, 0x61))

    @property
    def mode(self) -> str | None:                 # CF frames
        return {0x10: "heating", 0x19: "warm", 0x1D: "defrost"}.get(self.dec[10] & 0x1F)

    @property
    def pv_on(self) -> bool:                       # CE frames
        return (self.dec[10] & 0x40) == 0


def parse(frame: bytes) -> Frame:
    assert len(frame) == 32, "frame must be 32 bytes"
    dec = demask(frame)
    crc_frame = frame[30] | (frame[31] << 8)
    return Frame(raw=frame, dec=dec, crc_ok=(crc16_modbus(frame[:30]) == crc_frame))


# --------------------------------------------------------------------------
# Water temperature
# --------------------------------------------------------------------------
def water_temp_stateless(ce14: int, ce15: int) -> float:
    """Absolute temperature from a single CE frame. No memory; survives reboots."""
    fine = ce14 % 64
    center = 49.5 if ce15 == 206 else 55.5
    k = round(((WT_A - center) / WT_B - fine) / 64.0)
    t = WT_A - WT_B * (fine + 64 * k)
    if ce15 != 206:                                # wide band: full-byte tiebreak
        if ce14 >= 64 and t < 57.0:
            t = WT_A - WT_B * (fine + 64 * (k - 1))
        if ce14 < 64 and t > 57.5:
            t = WT_A - WT_B * (fine + 64 * (k + 1))
    return t


class ContinuityDecoder:
    """Incremental decoder: tracks ce[14] wraps like an encoder, re-anchors in the
    unambiguous ce[15]==206 band. ~0.3 °C once anchored."""

    def __init__(self):
        self.acc = 0
        self.prev_fine = None
        self.anchored = False

    def feed(self, ce14: int, ce15: int) -> float:
        fine = ce14 % 64
        if ce15 == 206:                            # absolute anchor
            self.acc = 64 * round((112.8 - fine) / 64.0)
            self.anchored = True
        elif not self.anchored:                    # cold start in wide band
            self.acc = 64 * round((51.9 - fine) / 64.0)
            self.anchored = True
        else:                                      # track wraps
            d = fine - self.prev_fine
            if d > 32:
                self.acc -= 64
            elif d < -32:
                self.acc += 64
        self.prev_fine = fine
        return WT_A - WT_B * (fine + self.acc)


if __name__ == "__main__":
    # tiny self-test: (ce14, ce15) -> expected ~temp, from the real capture
    samples = [(78, 205, 59.2), (73, 205, 60.0), (57, 205, 55.0),
               (196, 206, 47.0), (240, 206, 49.0)]
    cont = ContinuityDecoder()
    print(f"{'ce14':>5} {'ce15':>5} {'stateless':>10} {'continuity':>11} {'~display':>9}")
    for ce14, ce15, ref in samples:
        s = water_temp_stateless(ce14, ce15)
        c = cont.feed(ce14, ce15)
        print(f"{ce14:5d} {ce15:5d} {s:10.1f} {c:11.1f} {ref:9.1f}")
