"""Minimal PNG encoder (no Pillow): 32-bit BGRA rows (Windows DIB, top-down) or RGB -> PNG bytes."""
from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def bgra_to_rgb(buf: bytes) -> bytes:
    src = memoryview(buf)
    n = len(buf) // 4
    out = bytearray(n * 3)
    out[0::3] = src[2::4]
    out[1::3] = src[1::4]
    out[2::3] = src[0::4]
    return bytes(out)


def encode_rgb(width: int, height: int, rgb: bytes, level: int = 6) -> bytes:
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, level)) + _chunk(b"IEND", b"")


def encode_bgra(width: int, height: int, bgra: bytes) -> bytes:
    return encode_rgb(width, height, bgra_to_rgb(bgra))


def size_of(data: bytes) -> tuple[int, int] | None:
    """(width, height) of PNG/JPEG/GIF/BMP/WebP bytes, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", data[6:10])
    if data[:2] == b"BM":
        w, h = struct.unpack("<ii", data[18:26])
        return w, abs(h)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        return None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".bmp": "image/bmp",
        ".webp": "image/webp"}
