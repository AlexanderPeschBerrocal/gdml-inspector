"""A minimal PNG reader for the screenshot tests (8-bit RGB/RGBA, not interlaced),
so they need no imaging library."""

import struct
import zlib


def read_png(data):
    """(width, height, channels, rows) where each row is a bytes object."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    position, chunks = 8, {}
    idat = b""
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        body = data[position + 8 : position + 8 + length]
        if kind == b"IDAT":
            idat += body
        else:
            chunks[kind] = body
        position += 12 + length
    width, height, depth, colour, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", chunks[b"IHDR"]
    )
    assert depth == 8 and interlace == 0 and colour in (2, 6)
    channels = 3 if colour == 2 else 4
    stride = width * channels
    raw = zlib.decompress(idat)
    rows, previous = [], bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + predictor) & 255
        rows.append(bytes(line))
        previous = line
    return width, height, channels, rows


def pixel(rows, channels, x, y):
    return tuple(rows[y][x * channels : x * channels + channels])
