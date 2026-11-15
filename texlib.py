"""Parse / shrink SFV (UE 4.8-era, unversioned cooked) Texture2D .uasset files.

Layout found in SFV: one Texture2D export, mips stored after the export as
"payload at end of file" bulk data, offsets relative to BulkDataStartOffset.
"""
import struct

TAG = 0x9E2A83C1


class R:
    def __init__(s, d, p=0):
        s.d = d; s.p = p

    def i32(s):
        v = struct.unpack_from('<i', s.d, s.p)[0]; s.p += 4; return v

    def i64(s):
        v = struct.unpack_from('<q', s.d, s.p)[0]; s.p += 8; return v

    def fstr(s):
        n = s.i32()
        if n == 0:
            return ''
        if n < 0:
            b = s.d[s.p:s.p - 2 * n]; s.p += -2 * n; return b.decode('utf-16-le')[:-1]
        b = s.d[s.p:s.p + n]; s.p += n; return b[:-1].decode('latin1')


class Tex:
    pass


def parse(d):
    """d: at least header + export bytes. Returns Tex or raises ValueError."""
    r = R(d)
    if r.i32() & 0xffffffff != TAG or r.i32() != -5:
        raise ValueError('not uasset')
    r.p = 0x18
    t = Tex()
    t.total_header = r.i32()
    if r.fstr() != 'None':
        raise ValueError('folder')
    r.i32()  # flags
    ncount, noff = r.i32(), r.i32()
    t.export_count, t.export_off = r.i32(), r.i32()
    r.i32(); r.i32()  # imports
    r.i32()  # depends
    r.i32(); r.i32()  # string asset refs
    r.i32()  # thumbnail
    r.p += 16
    g = r.i32(); r.p += 8 * g
    r.p += 10; r.fstr()  # saved-by engine version
    r.i32()  # compression flags
    if r.i32() != 0:
        raise ValueError('compressed chunks')
    r.i32()  # package source
    for _ in range(r.i32()):
        r.fstr()
    if r.i32() != 0:
        raise ValueError('texture allocations')
    r.i32()  # asset registry
    t.bulk_start_at = r.p
    t.bulk_start = r.i64()
    r.p = noff
    t.names = [r.fstr() for _ in range(ncount)]
    if 'Texture2D' not in t.names:
        raise ValueError('not a Texture2D package')
    if t.export_count != 1:
        raise ValueError(f'{t.export_count} exports')
    r.p = t.export_off
    ex = [r.i32() for _ in range(6)]
    t.serial_size_at = r.p
    t.serial_size, t.serial_off = r.i32(), r.i32()
    if t.names[ex[3]] != t.names[0].rsplit('/', 1)[-1] and ex[3] >= len(t.names):
        raise ValueError('export name')
    end = t.serial_off + t.serial_size
    if end != t.bulk_start:
        raise ValueError('bulk does not follow export')
    # find platform data: FString pixel format name inside the export
    pf_names = [n for n in t.names if n.startswith('PF_')]
    if len(pf_names) != 1:
        raise ValueError(f'pixel formats {pf_names}')
    t.pf = pf_names[0]
    needle = struct.pack('<i', len(t.pf) + 1) + t.pf.encode() + b'\0'
    pos = d.find(needle, t.serial_off, end)
    if pos < 0:
        raise ValueError('pf string')
    t.size_at = pos - 12
    t.sx, t.sy, t.slices = struct.unpack_from('<iii', d, t.size_at)
    t.skip_at = pos - 16
    t.skip = struct.unpack_from('<i', d, t.skip_at)[0]
    r.p = pos + len(needle)
    t.pre_mips = r.i32()  # unknown int (0)
    t.nmips_at = r.p
    n = r.i32()
    t.mips = []
    for _ in range(n):
        at = r.p
        cooked, flags, count, disk = r.i32(), r.i32(), r.i32(), r.i32()
        off = r.i64()
        mx, my = r.i32(), r.i32()
        if cooked != 1 or count != disk or flags not in (1, 0x40, 0x41):
            raise ValueError(f'mip flags {cooked} {flags:#x} {count} {disk}')
        t.mips.append(dict(at=at, flags=flags, size=count, off=off, x=mx, y=my))
    t.mips_end = r.p
    if t.skip != r.p:  # SkipOffset points just past the mip array
        raise ValueError(f'skip {t.skip} != {r.p}')
    o = 0
    for m in t.mips:
        if m['off'] != o:
            raise ValueError('mip data not contiguous')
        o += m['size']
    t.bulk_size = o
    return t


def drop_count(t, max_dim):
    k = 0
    while k < len(t.mips) - 1 and max(t.mips[k]['x'], t.mips[k]['y']) > max_dim:
        k += 1
    return k


def shrink(d, max_dim):
    """Return (new_bytes, dropped_mips). Full file needed."""
    t = parse(d)
    k = drop_count(t, max_dim)
    if k == 0:
        return d, 0
    if len(d) != t.bulk_start + t.bulk_size + 4 or struct.unpack_from('<I', d, len(d) - 4)[0] != TAG:
        raise ValueError('unexpected file tail')
    rec = 32
    cut = rec * k
    dropped = sum(m['size'] for m in t.mips[:k])
    out = bytearray(d[:t.mips[0]['at']])
    for m in t.mips[k:]:
        b = bytearray(d[m['at']:m['at'] + rec])
        struct.pack_into('<q', b, 16, m['off'] - dropped)
        out += b
    out += d[t.mips_end:t.bulk_start]
    out += d[t.bulk_start + dropped:]
    # Same convention Capcom's cooked stage textures use: SizeX/Y keep the
    # full size, the int after the pixel-format string counts stripped mips.
    struct.pack_into('<i', out, t.nmips_at - 4, t.pre_mips + k)
    struct.pack_into('<i', out, t.skip_at, t.skip - cut)
    struct.pack_into('<i', out, t.nmips_at, len(t.mips) - k)
    struct.pack_into('<i', out, t.serial_size_at, t.serial_size - cut)
    struct.pack_into('<q', out, t.bulk_start_at, t.bulk_start - cut)
    out = bytes(out)
    t2 = parse(out)  # sanity
    assert t2.pre_mips == t.pre_mips + k and len(t2.mips) == len(t.mips) - k and len(out) == len(d) - cut - dropped
    return out, k


def needs_rescale(t, max_dim):
    """Single-mip texture bigger than max_dim (can't drop mips; must resample)."""
    import bcscale
    m = t.mips[0] if len(t.mips) == 1 else None
    return bool(m and max(m['x'], m['y']) > max_dim and m['flags'] == 1 and t.pf in bcscale.FORMATS
                and _pow2(m['x']) and _pow2(m['y']))


def _pow2(n):
    return n >= 4 and n & (n - 1) == 0


def rescale(d, max_dim):
    """Resample a single-mip texture down to <= max_dim. Returns (new_bytes, steps)."""
    import bcscale
    t = parse(d)
    if not needs_rescale(t, max_dim):
        return d, 0
    if len(d) != t.bulk_start + t.bulk_size + 4 or struct.unpack_from('<I', d, len(d) - 4)[0] != TAG:
        raise ValueError('unexpected file tail')
    m = t.mips[0]
    k = 0
    while max(m['x'], m['y']) >> k > max_dim and min(m['x'], m['y']) >> k > 4:
        k += 1
    data = d[t.bulk_start:t.bulk_start + m['size']]
    new, nw, nh = bcscale.downscale(data, t.pf, m['x'], m['y'], 1 << k)
    out = bytearray(d[:t.bulk_start]) + new + d[t.bulk_start + m['size']:]
    struct.pack_into('<ii', out, m['at'] + 8, len(new), len(new))
    struct.pack_into('<ii', out, m['at'] + 24, nw, nh)
    struct.pack_into('<i', out, t.nmips_at - 4, t.pre_mips + k)
    out = bytes(out)
    t2 = parse(out)
    assert t2.mips[0]['x'] == nw and t2.bulk_size == len(new) and len(out) == len(d) - m['size'] + len(new)
    return out, k
