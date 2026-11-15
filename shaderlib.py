"""Strip the unused DirectX 10 (SM4) shader set from SFV material packages.

Cooked SFV materials carry inline shader maps for two platforms:
PCD3D_SM5 (DirectX 11, what the game actually uses) and PCD3D_SM4 (DirectX 10).
Layout of the single export, after its tagged properties:
    ... int32 NumResources (=2) | resource SM5 | resource SM4 (to end of export)
Each resource begins: int32 bCooked=1, int32 bValid=1, int32 0, FGuid material id,
int32 QualityLevel, int32 FeatureLevel (3 = SM5, 2 = SM4), ...
Only packages that match this layout exactly are touched.
"""
import re
import struct

from texlib import R, TAG

SM5 = b"Platform=PCD3D_SM5"
SM4 = b"Platform=PCD3D_SM4"


class Pkg:
    pass


def summary(d):
    r = R(d)
    if r.i32() & 0xffffffff != TAG or r.i32() != -5:
        raise ValueError("not uasset")
    r.p = 0x18
    r.i32()
    if r.fstr() != "None":
        raise ValueError("folder")
    r.i32()
    k = Pkg()
    k.ncount, k.noff = r.i32(), r.i32()
    k.export_count, k.export_off = r.i32(), r.i32()
    r.i32(); r.i32(); r.i32(); r.i32(); r.i32(); r.i32()
    r.p += 16
    g = r.i32(); r.p += 8 * g
    r.p += 10; r.fstr()
    r.i32()
    if r.i32() != 0:
        raise ValueError("compressed chunks")
    r.i32()
    for _ in range(r.i32()):
        r.fstr()
    if r.i32() != 0:
        raise ValueError("texture allocations")
    r.i32()
    k.bulk_start_at = r.p
    k.bulk_start = r.i64()
    r.p = k.noff
    k.names = [r.fstr() for _ in range(k.ncount)]
    return k


def skip_props(d, p, names):
    while True:
        n = names[struct.unpack_from("<i", d, p)[0]]
        p += 8
        if n == "None":
            return p
        t = names[struct.unpack_from("<i", d, p)[0]]
        p += 8
        size = struct.unpack_from("<i", d, p)[0]
        p += 8
        if t == "StructProperty":
            p += 8
        elif t == "BoolProperty":
            p += 1
        elif t in ("ByteProperty", "ArrayProperty"):
            p += 8
        p += size


def plan(d):
    """-> (cut_start, cut_end, count_at, pkg) or None if this isn't a strippable material."""
    if SM4 not in d:
        return None
    k = summary(d)
    if k.export_count != 1:
        raise ValueError(f"{k.export_count} exports")
    r = R(d, k.export_off)
    r.p += 24
    k.serial_size_at = r.p
    k.serial_size, k.serial_off = r.i32(), r.i32()
    end = k.serial_off + k.serial_size
    if not (k.bulk_start in (end, 0)) or len(d) != end + 4:
        raise ValueError("unexpected file layout after export")
    p = skip_props(d, k.serial_off, k.names)
    # NumResources (=2) followed by the first resource (bCooked=1, bValid=1). Some
    # materials have extra data between the properties and the count, so try each
    # candidate and keep the one whose matching second resource exists.
    pat = struct.pack("<iii", 2, 1, 1)
    found = None
    q = d.find(pat, p, end)
    while q != -1 and q < p + 65536:
        r1 = q + 4
        head = bytearray(d[r1:r1 + 36])
        fl1 = struct.unpack_from("<i", head, 32)[0]
        if fl1 in (2, 3):
            struct.pack_into("<i", head, 32, 5 - fl1)  # the other feature level (3 <-> 2)
            other = [m.start() for m in re.finditer(re.escape(bytes(head)), d)]
            if len(other) == 1 and other[0] > r1:
                found = (q, r1, fl1, other[0])
                break
        q = d.find(pat, q + 4, end)
    if not found:
        raise ValueError("resource count not found")
    count_at, r1, fl1, r2 = found
    a, b = d[r1:r2], d[r2:end]
    if fl1 == 3:  # SM5 first, SM4 second -> drop [r2, end)
        if a.count(SM5) < 1 or SM4 in a or b.count(SM4) < 1 or SM5 in b:
            raise ValueError("platform strings don't match layout")
        return r2, end, count_at, k
    if a.count(SM4) < 1 or SM5 in a or b.count(SM5) < 1 or SM4 in b:
        raise ValueError("platform strings don't match layout")
    return r1, r2, count_at, k


def strip(d):
    """Return (new_bytes, removed) or (d, 0)."""
    pl = plan(d)
    if not pl:
        return d, 0
    s, e, count_at, k = pl
    cut = e - s
    out = bytearray(d[:s] + d[e:])
    struct.pack_into("<i", out, count_at, 1)
    struct.pack_into("<i", out, k.serial_size_at, k.serial_size - cut)
    if k.bulk_start:
        struct.pack_into("<q", out, k.bulk_start_at, k.bulk_start - cut)
    out = bytes(out)
    if SM4 in out or SM5 not in out:
        raise ValueError("strip check failed")
    return out, cut
