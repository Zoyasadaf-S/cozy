"""Generates the PWA icons with the standard library only (no Pillow). Run once: python scripts/make_icons.py"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "cozy" / "static"


def png(size: int) -> bytes:
    bg, ring, hole = (11, 15, 20), (61, 220, 151), (11, 15, 20)
    c, r_out, r_in = size / 2, size * 0.34, size * 0.17
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            d2 = (x - c) ** 2 + (y - c) ** 2
            rows += bytes(hole if d2 < r_in ** 2 else ring if d2 < r_out ** 2 else bg)
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    for s in (192, 512):
        (OUT / f"icon-{s}.png").write_bytes(png(s))
        print("wrote", OUT / f"icon-{s}.png")
