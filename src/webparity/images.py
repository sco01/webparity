# SPDX-License-Identifier: BSD-2-Clause
"""Screenshots as grey-level grids, in plain Python (stdlib only: zlib and struct).

``grid(png)`` decodes an 8-bit RGB/RGBA PNG without interlace into a grid of cell x cell pixels with the mean and the
mean of squares per cell (for the spread). ``fingerprint`` is the 32 x 20 fingerprint the regression watch compares from
build to build, ``spread`` the grey-level standard deviation (small = a single colour), ``compare`` the block comparison
of Ladybird against the reference with moving blocks masked, and ``regions`` pairs iframes or canvases in the two
browsers and measures the spread inside each.
"""

from __future__ import annotations

import struct
import zlib


def grid(png: bytes | None, cell: int = 8) -> dict | None:
    """Grey levels as a grid: ``{"w", "h", "cell", "gw", "gh", "m": means, "q": means of squares}``, or None."""
    if not png or png[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, channels, w, h = 8, [], 3, 0, 0
    while pos + 8 <= len(png):
        n, kind = struct.unpack_from(">I4s", png, pos)
        data = png[pos + 8 : pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", data)
            if depth != 8 or colour not in (2, 6) or interlace:
                return None
            channels = 4 if colour == 6 else 3
        elif kind == b"IDAT":
            idat.append(data)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if not w or not idat:
        return None
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    row = w * channels
    if len(raw) < h * (row + 1):
        return None
    gw, gh = (w + cell - 1) // cell, (h + cell - 1) // cell
    s, q, n = [0.0] * (gw * gh), [0.0] * (gw * gh), [0] * (gw * gh)
    prev = bytearray(row)
    for y in range(h):
        f, cur = raw[y * (row + 1)], bytearray(raw[y * (row + 1) + 1 : (y + 1) * (row + 1)])
        if f == 1:
            for i in range(channels, row):
                cur[i] = (cur[i] + cur[i - channels]) & 255
        elif f == 2:
            cur = bytearray((a + b) & 255 for a, b in zip(cur, prev, strict=False))
        elif f == 3:
            for i in range(row):
                cur[i] = (cur[i] + ((cur[i - channels] if i >= channels else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(channels):  # a = c = 0: the predictor is b
                cur[i] = (cur[i] + prev[i]) & 255
            for i in range(channels, row):
                a, b, c = cur[i - channels], prev[i], prev[i - channels]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - c - c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = cur
        base = (y // cell) * gw
        for x in range(0, w, 2):  # every second pixel is enough for a grey-level mean
            i = x * channels
            g = 0.299 * cur[i] + 0.587 * cur[i + 1] + 0.114 * cur[i + 2]
            k = base + x // cell
            s[k] += g
            q[k] += g * g
            n[k] += 1
    return {
        "w": w,
        "h": h,
        "cell": cell,
        "gw": gw,
        "gh": gh,
        "m": [a / c if c else 0 for a, c in zip(s, n, strict=True)],
        "q": [a / c if c else 0 for a, c in zip(q, n, strict=True)],
    }


def fingerprint(g: dict | None, width: int = 32, height: int = 20) -> list[int] | None:
    """The 32 x 20 fingerprint (the regression watch's night-to-night comparison) from a grid."""
    if not g:
        return None
    s, n = [0.0] * (width * height), [0] * (width * height)
    for y in range(g["gh"]):
        for x in range(g["gw"]):
            k = (y * height // g["gh"]) * width + x * width // g["gw"]
            s[k] += g["m"][y * g["gw"] + x]
            n[k] += 1
    return [round(a / c) if c else 0 for a, c in zip(s, n, strict=True)]


def spread(g: dict | None, box: list[int] | None = None) -> float | None:
    """Grey-level standard deviation inside box [x, y, w, h] (pixels) or the whole image. Small = one colour."""
    if not g:
        return None
    c = g["cell"]
    if box is None:
        x0, y0, x1, y1 = 0, 0, g["gw"], g["gh"]
    else:  # whole cells inside the box only
        x0, y0 = max(0, box[0] // c + 1), max(0, box[1] // c + 1)
        x1, y1 = min(g["gw"], (box[0] + box[2]) // c - 1), min(g["gh"], (box[1] + box[3]) // c - 1)
    if x1 <= x0 or y1 <= y0:
        return None
    m = q = 0.0
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            k = y * g["gw"] + x
            m += g["m"][k]
            q += g["q"][k]
            n += 1
    m, q = m / n, q / n
    return round(max(0.0, q - m * m) ** 0.5, 1)


def blocks(g: dict, b: int = 4) -> tuple[int, int, list[float]]:
    """Mean per block of b x b cells (32 px with cell 8)."""
    bw, bh = g["gw"] // b, g["gh"] // b
    out = []
    for by in range(bh):
        for bx in range(bw):
            v = 0.0
            for y in range(by * b, by * b + b):
                k = y * g["gw"] + bx * b
                v += sum(g["m"][k : k + b])
            out.append(v / (b * b))
    return bw, bh, out


def compare(lb1, lb2, ref1, ref2, lb_reload=None, ref_reload=None, mask_limit=12, diff_limit=30) -> dict | None:
    """Ladybird against the reference in 32 px blocks. Blocks that move (between two screenshots in the same browser, or
    between the first load and the reload in either browser) are masked. share = differing blocks / unmasked blocks."""
    if not (lb1 and ref1) or (lb1["gw"], lb1["gh"]) != (ref1["gw"], ref1["gh"]):
        return None
    bw, bh, a = blocks(lb1)
    _, _, c = blocks(ref1)
    if not a:
        return None
    mask = [False] * len(a)
    for g, base in ((lb2, a), (ref2, c), (lb_reload, a), (ref_reload, c)):
        if g and (g["gw"], g["gh"]) == (lb1["gw"], lb1["gh"]):
            _, _, o = blocks(g)
            mask = [m or abs(x - y) > mask_limit for m, x, y in zip(mask, o, base, strict=True)]
    differing = [i for i, (x, y, m) in enumerate(zip(a, c, mask, strict=True)) if not m and abs(x - y) > diff_limit]
    unmasked = len(a) - sum(mask)
    out = {
        "share": round(len(differing) / unmasked, 3) if unmasked else None,
        "masked": round(sum(mask) / len(a), 3),
        "mean": round(sum(abs(x - y) for x, y in zip(a, c, strict=True)) / (255 * len(a)), 3),
    }
    if differing:
        xs, ys = [i % bw for i in differing], [i // bw for i in differing]
        out["area"] = [min(xs) * 32, min(ys) * 32, (max(xs) - min(xs) + 1) * 32, (max(ys) - min(ys) + 1) * 32]
    return out


def regions(g_lb, g_ref, lb_boxes, ref_boxes) -> list[dict]:
    """Pairs iframes/canvases in Ladybird and the reference (about the same box) and measures the spread in each."""
    out = []
    width = (g_lb or {}).get("w") or 1440
    height = (g_lb or {}).get("h") or 900
    for a in lb_boxes or []:
        ra = a.get("box") if isinstance(a, dict) else a
        if not isinstance(ra, list) or len(ra) != 4:
            continue
        for c in ref_boxes or []:
            rc = c.get("box") if isinstance(c, dict) else c
            if not isinstance(rc, list) or len(rc) != 4:
                continue
            if (
                abs(ra[0] - rc[0]) < 60
                and abs(ra[1] - rc[1]) < 60
                and abs(ra[2] - rc[2]) < 80
                and abs(ra[3] - rc[3]) < 80
            ):
                x0, y0 = max(0, ra[0]), max(0, ra[1])
                box = [x0, y0, min(ra[0] + ra[2], width) - x0, min(ra[1] + ra[3], height) - y0]
                d = {"box": box, "ladybird": spread(g_lb, box), "reference": spread(g_ref, box)}
                if isinstance(a, dict):
                    d.update(
                        {
                            "src": a.get("src"),
                            "ladybird_elements": a.get("elements"),
                            "reference_elements": c.get("elements"),
                        }
                    )
                out.append(d)
                break
    return out


def encode(width: int, height: int, pixel) -> bytes:
    """A PNG (8-bit RGB, filter 0) from ``pixel(x, y) -> (r, g, b)``; for test fixtures and tools."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )
