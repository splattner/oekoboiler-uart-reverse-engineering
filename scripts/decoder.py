#!/usr/bin/env python3
"""
Reference decoder for the Ökoboiler internal UART protocol.

Pure-Python implementation of everything the ESPHome lambda does:
  - frame framing + XOR demasking + CRC-16/Modbus check
  - operating mode, PV mode
  - all five temperature channels (tank + four heat-pump sensors)

Use it to sanity-check captures, or as the spec for a port to another platform.
See PROTOCOL.md for the field reference.
"""
from __future__ import annotations
from dataclasses import dataclass

# Second-layer key: a FIXED XOR per payload position, applied on top of the
# per-frame raw[5] mask. CF frames send most of it in the clear (their payload is
# mostly zero); KEY[14] differs from cf[14] because cf[14] carries a timer.
KEY = {
    14: 0x1D, 15: 0xCF,   # tank / water
    16: 0x7E, 17: 0x90,   # return gas   (provisional name)
    18: 0x0F, 19: 0x24,   # ambient      (provisional name)
    20: 0x60, 21: 0xA1,   # exhaust      (provisional name)
    22: 0xEC, 23: 0x5E,   # coil         (provisional name)
}

# (low byte index, name) of each int16 temperature channel in CE frames
TEMP_CHANNELS = {
    14: "water",
    16: "return_gas",
    18: "ambient",
    20: "exhaust",
    22: "coil",
}


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


def temp16(dec: bytes, lo: int) -> float:
    """Temperature of the channel whose low byte sits at dec[lo] (CE frames).

    Remove the fixed key from both bytes, read them as a signed little-endian
    int16, and scale by 0.1 °C. Stateless and valid over the whole range.
    """
    v = (dec[lo] ^ KEY[lo]) | ((dec[lo + 1] ^ KEY[lo + 1]) << 8)
    if v >= 0x8000:
        v -= 0x10000
    return v / 10.0


def encode_temp16(t: float, lo: int) -> tuple[int, int]:
    """Inverse of temp16(): (dec[lo], dec[lo+1]) for a temperature. Handy for tests."""
    v = round(t * 10) & 0xFFFF
    return (v & 0xFF) ^ KEY[lo], (v >> 8) ^ KEY[lo + 1]


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

    @property
    def temperatures(self) -> dict[str, float]:    # CE frames
        return {name: temp16(self.dec, lo) for lo, name in TEMP_CHANNELS.items()}


def parse(frame: bytes) -> Frame:
    assert len(frame) == 32, "frame must be 32 bytes"
    dec = demask(frame)
    crc_frame = frame[30] | (frame[31] << 8)
    return Frame(raw=frame, dec=dec, crc_ok=(crc16_modbus(frame[:30]) == crc_frame))


def _build_ce_frame(temps: dict[str, float], mask: int = 0x5A) -> bytes:
    """Synthesize a CRC-valid CE frame (for the self-test only)."""
    dec = bytearray(32)
    dec[0], dec[1] = 0x64, 0x41
    dec[6:10] = bytes((0xF2, 0xD4, 0xF9, 0x61))
    dec[10], dec[11] = 0x19, 0xCE
    for lo, name in TEMP_CHANNELS.items():
        dec[lo], dec[lo + 1] = encode_temp16(temps[name], lo)
    raw = bytearray(dec)
    for i in range(2, 30):
        raw[i] = dec[i] ^ mask
    raw[5] = mask                      # dec[5] = 0, so raw[5] is the mask itself
    crc = crc16_modbus(bytes(raw[:30]))
    raw[30], raw[31] = crc & 0xFF, crc >> 8
    return bytes(raw)


if __name__ == "__main__":
    # 1) real (ce14, ce15) pairs captured next to the OCR'd front-panel display
    #    (whole degrees, so expect agreement within ~0.5 °C)
    samples = [(78, 205, 59.2), (73, 205, 60.0), (57, 205, 55.0),
               (196, 206, 47.0), (240, 206, 49.0)]
    print(f"{'ce14':>5} {'ce15':>5} {'decoded':>8} {'display':>8}")
    for ce14, ce15, ref in samples:
        dec = bytearray(32)
        dec[14], dec[15] = ce14, ce15
        t = temp16(bytes(dec), 14)
        print(f"{ce14:5d} {ce15:5d} {t:8.1f} {ref:8.1f}")
        assert abs(t - ref) < 1.0

    # 2) round trip through a full synthetic frame (mask, CRC, all channels)
    want = {"water": 49.6, "return_gas": 1.6, "ambient": 13.5, "exhaust": 54.8, "coil": -3.2}
    f = parse(_build_ce_frame(want))
    assert f.crc_ok and f.signature_ok and f.is_ce
    assert all(abs(f.temperatures[k] - v) < 1e-9 for k, v in want.items()), f.temperatures
    print("round trip OK:", f.temperatures)
