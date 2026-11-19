"""產生 assets/app.ico（深藍圓角底 + 播放三角 + 底部亮條）。只用標準庫。"""
import struct, zlib

SIZES = [16, 24, 32, 48, 64, 128, 256]


def img(n: int) -> bytes:
    r = max(2, n * 0.18)
    cy = n / 2.0
    rows = []
    for y in range(n):
        row = bytearray()
        for x in range(n):
            b, g, rr, a = 0, 0, 0, 0
            inside = True
            # 圓角：四角只有在圓r內才保留，否則視為圓角外
            for qx, qy in ((r, r), (n - r, r), (r, n - r), (n - r, n - r)):
                near_x = (x < r) if qx < n / 2 else (x > n - r)
                near_y = (y < r) if qy < n / 2 else (y > n - r)
                if near_x and near_y:
                    if (x - qx) ** 2 + (y - qy) ** 2 > r * r:
                        inside = False
            if inside:
                t = y / max(1, n - 1)
                b = int(135 + 55 * (1 - t))
                g = int(70 + 40 * (1 - t))
                rr = int(45 + 30 * (1 - t))
                a = 255
                tx0, tx1 = n * 0.36, n * 0.71
                ty0, ty1 = n * 0.27, n * 0.73
                if tx0 <= x <= tx1 and ty0 <= y <= ty1:
                    mx = (x - tx0) / (tx1 - tx0)
                    half = (1 - mx) * (ty1 - ty0) / 2
                    if abs(y - cy) <= half:
                        b, g, rr, a = 248, 250, 252, 255
                if n * 0.79 <= y <= n * 0.865 and n * 0.25 <= x <= n * 0.75:
                    b, g, rr, a = 60, 190, 252, 255
            row += bytes((b, g, rr, a))
        rows.append(bytes(row))
    body = b"".join(reversed(rows))
    mask = bytes(((n + 31) // 32) * 4 * n)
    hdr = struct.pack("<IiiHHIIiiII", 40, n, n, 1, 32, 0, len(body), 0, 0, 0, 0)
    return hdr + body + mask


def build(path="assets/app.ico", preview=".scratch/icon-preview.png"):
    imgs = [(s, img(s)) for s in SIZES]
    hdr = struct.pack("<HHH", 0, 1, len(imgs))
    ents = bytearray()
    data = bytearray()
    off = 6 + 16 * len(imgs)
    for s, bdt in imgs:
        w = 0 if s >= 256 else s
        ents += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(bdt), off)
        data += bdt
        off += len(bdt)
    blob = bytes(hdr + ents + data)
    open(path, "wb").write(blob)

    d = imgs[-1][1]
    W = H = 256
    px = d[40 : 40 + W * H * 4]
    rows = [px[i * W * 4 : (i + 1) * W * 4] for i in range(H)][::-1]
    raw = b""
    for rrow in rows:
        o = bytearray()
        for x in range(W):
            bb, gg, rr, aa = rrow[x * 4 : x * 4 + 4]
            o += bytes((rr, gg, bb, aa))
        raw += b"\x00" + bytes(o)

    def chunk(t, dd):
        return struct.pack(">I", len(dd)) + t + dd + struct.pack(">I", zlib.crc32(t + dd) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    open(preview, "wb").write(png)
    return len(blob)


if __name__ == "__main__":
    print("ico bytes:", build())
