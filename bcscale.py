"""Downscale block-compressed textures (DXT1 / DXT5 / BC5) by a power of two.

Used for SFV textures that were cooked with a single mip level, where dropping
mips isn't possible: decode -> box filter -> re-encode. Needs numpy.
"""
import numpy as np

FORMATS = {"PF_DXT1": 8, "PF_DXT5": 16, "PF_BC5": 16}  # bytes per 4x4 block


# ---------- decode ----------

def _rgb565(c):
    c = c.astype(np.uint32)
    r = (c >> 11) & 31
    g = (c >> 5) & 63
    b = c & 31
    return np.stack([(r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31], -1).astype(np.float32)


def _bits(blocks, start, nbytes, width, count):
    v = np.zeros(len(blocks), np.uint64)
    for i in range(nbytes):
        v |= blocks[:, start + i].astype(np.uint64) << np.uint64(8 * i)
    sh = np.arange(count, dtype=np.uint64) * np.uint64(width)
    return ((v[:, None] >> sh) & np.uint64((1 << width) - 1)).astype(np.int64)


def _dec_bc1(b, force4=False):
    c0 = b[:, 0].astype(np.uint16) | (b[:, 1].astype(np.uint16) << 8)
    c1 = b[:, 2].astype(np.uint16) | (b[:, 3].astype(np.uint16) << 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    four = (c0 > c1) | force4
    f = four[:, None]
    p2 = np.where(f, (2 * p0 + p1) / 3, (p0 + p1) / 2)
    p3 = np.where(f, (p0 + 2 * p1) / 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)                          # N,4,3
    alpha = np.full((len(b), 4), 255.0, np.float32)
    alpha[:, 3] = np.where(four, 255.0, 0.0)
    idx = _bits(b, 4, 4, 2, 16)                                  # N,16
    rgb = np.take_along_axis(pal, idx[:, :, None].repeat(3, 2), 1)
    a = np.take_along_axis(alpha, idx, 1)
    return rgb, a


def _dec_bc4(b):
    a0 = b[:, 0].astype(np.float32)
    a1 = b[:, 1].astype(np.float32)
    eight = (a0 > a1)[:, None]
    i = np.arange(2, 8, dtype=np.float32)
    v8 = ((8 - i) * a0[:, None] + (i - 1) * a1[:, None]) / 7
    i6 = np.arange(2, 6, dtype=np.float32)
    v6 = ((6 - i6) * a0[:, None] + (i6 - 1) * a1[:, None]) / 5
    v6 = np.concatenate([v6, np.zeros((len(b), 1)), np.full((len(b), 1), 255.0)], 1)
    pal = np.concatenate([a0[:, None], a1[:, None], np.where(eight, v8, v6)], 1)
    idx = _bits(b, 2, 6, 3, 16)
    return np.take_along_axis(pal, idx, 1)


def decode(data, pf, w, h):
    bs = FORMATS[pf]
    b = np.frombuffer(data, np.uint8).reshape(-1, bs)
    if pf == "PF_DXT1":
        rgb, a = _dec_bc1(b)
        px = np.concatenate([rgb, a[:, :, None]], 2)
    elif pf == "PF_DXT5":
        a = _dec_bc4(b[:, :8])
        rgb, _ = _dec_bc1(b[:, 8:], force4=True)
        px = np.concatenate([rgb, a[:, :, None]], 2)
    else:  # BC5: two BC4 channels
        px = np.stack([_dec_bc4(b[:, :8]), _dec_bc4(b[:, 8:])], 2)
    c = px.shape[2]
    return px.reshape(h // 4, w // 4, 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(h, w, c).astype(np.float32)


# ---------- encode ----------

def _pack_bits(idx, width):
    v = np.zeros(len(idx), np.uint64)
    for i in range(idx.shape[1]):
        v |= idx[:, i].astype(np.uint64) << np.uint64(width * i)
    return v


def _enc_bc4(v):
    """v: N,16 floats 0..255 -> N,8 bytes (8-value mode)."""
    hi = np.clip(np.round(v.max(1)), 0, 255)
    lo = np.clip(np.round(v.min(1)), 0, 255)
    span = np.maximum(hi - lo, 1e-6)
    k = np.clip(np.round((hi[:, None] - v) / span[:, None] * 7), 0, 7).astype(np.int64)
    idx = np.where(k == 0, 0, np.where(k == 7, 1, k + 1))
    idx[hi == lo] = 0
    out = np.zeros((len(v), 8), np.uint8)
    out[:, 0] = hi
    out[:, 1] = lo
    bits = _pack_bits(idx, 3)
    for i in range(6):
        out[:, 2 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(255)).astype(np.uint8)
    return out


def _to565(c):
    c = np.clip(np.round(c), 0, 255)
    return ((np.round(c[:, 0] * 31 / 255).astype(np.uint32) << 11) |
            (np.round(c[:, 1] * 63 / 255).astype(np.uint32) << 5) |
            np.round(c[:, 2] * 31 / 255).astype(np.uint32))


def _enc_bc1(rgb):
    """rgb: N,16,3 -> N,8 bytes (always 4-colour mode)."""
    mn, mx = rgb.min(1), rgb.max(1)
    # pick the diagonal of the bounding box that follows the colour trend
    ref = np.argmax(mx - mn, 1)
    cen = rgb - rgb.mean(1, keepdims=True)
    refv = np.take_along_axis(cen, ref[:, None, None].repeat(16, 1), 2)  # N,16,1
    cov = (cen * refv).sum(1)                                            # N,3
    e0 = np.where(cov >= 0, mx, mn)
    e1 = np.where(cov >= 0, mn, mx)
    c0, c1 = _to565(e0), _to565(e1)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)   # N,4,3
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(3)         # N,16,4
    idx = d.argmin(2)
    idx[c0 == c1] = 0
    out = np.zeros((len(rgb), 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 255, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 255, c1 >> 8
    bits = _pack_bits(idx, 2)
    for i in range(4):
        out[:, 4 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(255)).astype(np.uint8)
    return out


def encode(img, pf):
    h, w, c = img.shape
    px = img.reshape(h // 4, 4, w // 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(-1, 16, c)
    if pf == "PF_DXT1":
        out = _enc_bc1(px[:, :, :3])
    elif pf == "PF_DXT5":
        out = np.concatenate([_enc_bc4(px[:, :, 3]), _enc_bc1(px[:, :, :3])], 1)
    else:
        out = np.concatenate([_enc_bc4(px[:, :, 0]), _enc_bc4(px[:, :, 1])], 1)
    return out.tobytes()


def downscale(data, pf, w, h, factor):
    """Return (new_bytes, new_w, new_h)."""
    img = decode(data, pf, w, h)
    nw, nh = w // factor, h // factor
    c = img.shape[2]
    img = img.reshape(nh, factor, nw, factor, c).mean((1, 3))
    return encode(img, pf), nw, nh
