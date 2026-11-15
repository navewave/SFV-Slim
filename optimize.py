"""SFV-Slim Optimized: keep every costume, shrink oversized textures.

Drops the top mip level(s) of Texture2D assets bigger than --max (default 2048),
using the same "stripped mips" convention Capcom's cooked stage textures already
use. Rewritten entries are re-compressed and re-encrypted exactly like the
originals, so the paks keep their normal format.

Needs aes_key.txt (the game's pak key, which you supply) next to this script and
pycryptodome (pip install pycryptodome).
"""
import argparse
import collections
import hashlib
import os
import re
import shutil
import struct
import sys
import time
import zlib

import sfv_slim as S
import shaderlib
import texlib

HERE = S.HERE
KEY_FILE = os.path.join(HERE, "aes_key.txt")
BLOCK = 65536
# costume textures: /chara/<code>/skelmesh/<nn>/...
COSTUME = re.compile(r"/chara/[^/]+/skelmesh/")
TEX_TYPES = ("normal", "sss", "srma", "color", "mask")


def tex_type(path):
    name = path.rsplit("/", 1)[-1]
    for t in TEX_TYPES:
        if t in name:
            return t
    return "other"


STAGE = re.compile(r"/content/(stage|maps)/")


def cap_for(path, caps):
    """Cap for this asset, or None to leave it alone."""
    if COSTUME.search(path):
        return caps.get(tex_type(path), caps["default"])
    if STAGE.search(path):
        return caps.get("stage")
    return None


def cipher():
    try:
        from Crypto.Cipher import AES
    except ImportError:
        sys.exit("Needs pycryptodome:  python -m pip install --user pycryptodome")
    if not os.path.exists(KEY_FILE):
        sys.exit(f"Put the game's AES key in {KEY_FILE}")
    key = open(KEY_FILE, "rb").read().strip()
    if len(key) != 32:
        sys.exit("aes_key.txt must hold the 32-character key")
    return AES.new(key, AES.MODE_ECB)


def _pad(b):
    return b + b"\0" * (-len(b) % 16)


def read_entry(aes, f, e, limit=None):
    """Decrypt + decompress an entry (optionally just the first `limit` bytes)."""
    if e.comp:
        out = []
        n = 0
        for a, b in e.blocks:
            f.seek(a)
            out.append(zlib.decompress(aes.decrypt(f.read((b - a + 15) // 16 * 16))[:b - a]))
            n += len(out[-1])
            if limit and n >= limit:
                break
        d = b"".join(out)
    else:
        f.seek(e.offset + len(e.header(0)))
        n = e.size if not limit else min(e.size, limit)
        d = aes.decrypt(f.read((n + 15) // 16 * 16))[:n]
    if not limit and len(d) != e.usize:
        raise ValueError(f"{e.name}: size mismatch")
    return d


def encode_entry(aes, e, data, offset):
    """Build a compressed+encrypted replacement entry stored at `offset`."""
    chunks = [zlib.compress(data[i:i + BLOCK], 6) for i in range(0, len(data), BLOCK)]
    ne = S.Entry()
    ne.name, ne.offset, ne.usize, ne.comp, ne.enc = e.name, offset, len(data), 1, 1
    ne.bsize = BLOCK if len(data) > BLOCK else len(data)
    hlen = 8 + 8 + 8 + 4 + 20 + 4 + 16 * len(chunks) + 1 + 4
    pos = offset + hlen
    blocks, stored = [], []
    for c in chunks:
        blocks.append((pos, pos + len(c)))
        enc = aes.encrypt(_pad(c))
        stored.append(enc)
        pos += len(enc)
    body = b"".join(stored)
    ne.blocks, ne.size, ne.sha1 = blocks, len(body), hashlib.sha1(body).digest()
    assert len(ne.header(0)) == hlen
    return ne, body


def plan(aes, path, caps, shaders=False, only=None):
    """-> (mount, ents, nxt, idx_off, {id(e): (entry, kind, cap)})"""
    mount, ents, idx_off = S.read_pak(path)
    by_off = sorted({e.offset for e in ents})
    nxt = {o: (by_off[i + 1] if i + 1 < len(by_off) else idx_off) for i, o in enumerate(by_off)}
    counts = collections.Counter(e.offset for e in ents)
    shared = {o for o, c in counts.items() if c > 1}
    todo = {}
    with open(path, "rb") as f:
        for e in ents:
            fp = S.full_path(mount, e.name)
            if e.usize < 100_000 or not fp.endswith(".uasset") or e.offset in shared:
                continue
            if only and not only.search(fp):
                continue
            cap = cap_for(fp, caps)
            if cap is None and not shaders:
                continue
            head = read_entry(aes, f, e, limit=BLOCK)
            if shaders and shaderlib.SM5 in head:
                todo[id(e)] = (e, "shader", None)
                continue
            if cap is None or e.usize < 300_000:
                continue
            try:
                t = texlib.parse(head)
            except ValueError:
                continue
            if texlib.drop_count(t, cap) or texlib.needs_rescale(t, cap):
                todo[id(e)] = (e, "tex", cap)
    return mount, ents, nxt, idx_off, todo


def write(aes, src, dst, mount, ents, nxt, todo, skipped=None):
    skipped = [] if skipped is None else skipped
    moved = {}
    new_ents = {}
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for e in sorted(ents, key=lambda e: e.offset):
            if e.offset in moved:
                continue
            new_off = fo.tell()
            data = None
            if id(e) in todo:
                _, kind, cap = todo[id(e)]
                orig = read_entry(aes, fi, e)
                if kind == "tex":
                    try:
                        data, _k = texlib.shrink(orig, cap)
                        if not _k:
                            data, _k = texlib.rescale(orig, cap)
                    except (ValueError, AssertionError) as ex:
                        skipped.append((e.name, str(ex)))
                        _k = 0
                    if not _k:
                        data = None
                else:
                    try:
                        data, cut = shaderlib.strip(orig)
                    except ValueError as ex:
                        skipped.append((e.name, str(ex)))
                        cut = 0
                    if not cut:
                        data = None
            if data is not None:
                ne, body = encode_entry(aes, e, data, new_off)
                fo.write(ne.header(0))
                fo.write(body)
                new_ents[id(e)] = ne
            else:
                fi.seek(e.offset)
                hlen = len(e.header(0))
                if fi.read(hlen) != e.header(0):
                    raise ValueError(f"inline header mismatch for {e.name}")
                fo.write(S._shift(e, new_off - e.offset, new_off).header(0))
                remaining = nxt[e.offset] - e.offset - hlen
                while remaining:
                    chunk = fi.read(min(remaining, 8 << 20))
                    if not chunk:
                        raise IOError("unexpected EOF")
                    fo.write(chunk)
                    remaining -= len(chunk)
            moved[e.offset] = new_off
        parts = []
        for e in ents:
            ne = new_ents.get(id(e)) or S._shift(e, moved[e.offset] - e.offset, moved[e.offset])
            parts.append(S._wstr(ne.name) + ne.header(ne.offset))
        idx = S._wstr(mount) + struct.pack("<i", len(ents)) + b"".join(parts)
        idx_off = fo.tell()
        fo.write(idx)
        fo.write(struct.pack("<IiQQ", S.PAK_MAGIC, 3, idx_off, len(idx)) + hashlib.sha1(idx).digest())
    return new_ents


def verify(aes, path, check_caps):
    """Re-read rebuilt pak: every entry decrypts/decompresses, hashes match, textures parse."""
    mount, ents, _ = S.read_pak(path)
    with open(path, "rb") as f:
        for e in ents:
            if e.name not in check_caps:
                continue
            max_dim = check_caps[e.name]
            f.seek(e.blocks[0][0] if e.comp else e.offset + len(e.header(0)))
            if hashlib.sha1(f.read(e.size)).digest() != e.sha1:
                raise ValueError(f"hash mismatch {e.name}")
            d = read_entry(aes, f, e)
            if max_dim is None:
                if shaderlib.SM4 in d or shaderlib.SM5 not in d:
                    raise ValueError(f"shader strip check failed {e.name}")
                continue
            t = texlib.parse(d)
            if max(t.mips[0]["x"], t.mips[0]["y"]) > max_dim:
                raise ValueError(f"not shrunk {e.name}")
    return len(ents)


def optimize_pak(aes, pak, caps, backup=None, dry=False, shaders=False, only=None):
    path = os.path.join(S.PAKS, pak)
    mount, ents, nxt, idx_off, todo = plan(aes, path, caps, shaders, only)
    if not todo:
        return 0
    before = os.path.getsize(path)
    if dry:
        nt = sum(1 for v in todo.values() if v[1] == "tex")
        print(f"  {pak:34} {nt:5} textures to shrink, {len(todo) - nt:5} materials to check")
        return len(todo)
    t0 = time.time()
    print(f"{pak}: processing {len(todo)} files ({S.human(before)}) ...", flush=True)
    tmp = path + ".opt.tmp"
    skipped = []
    try:
        changed = write(aes, path, tmp, mount, ents, nxt, todo, skipped)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if not changed:  # nothing actually needed changing (e.g. already optimized)
        os.remove(tmp)
        print("  already optimized, left as is")
        return 0
    for name, why in skipped:
        print(f"  left unchanged (unexpected layout): {name}: {why}")
    verify(aes, tmp, {e.name: c for e, k, c in todo.values() if id(e) in changed})
    m2, e2, _ = S.read_pak(tmp)
    if len(e2) != len(ents) or m2 != mount:
        os.remove(tmp)
        sys.exit(f"Verification of rebuilt {pak} failed; original left untouched.")
    if backup:
        os.makedirs(backup, exist_ok=True)
        shutil.move(path, os.path.join(backup, pak))
    os.replace(tmp, path)
    after = os.path.getsize(path)
    print(f"  done in {time.time() - t0:.0f}s: {S.human(before)} -> {S.human(after)}")
    st = S.load_state()
    prev = st["applied"].get(pak, {})
    st["applied"][pak] = {"before": prev.get("before", before), "after": after,
                          "tiers": prev.get("tiers", []), "optimized": caps,
                          "shaders": shaders or prev.get("shaders", False), "time": time.ctime()}
    S.save_state(st)
    return len(changed)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max", type=int, default=2048, help="default largest texture size to keep (2048)")
    ap.add_argument("--stage", type=int, help="also cap stage textures at this size (default: leave stages alone)")
    for t in TEX_TYPES:
        ap.add_argument(f"--{t}", type=int, help=f"cap for *{t}* textures (default: --max)")
    ap.add_argument("--paks", help="comma-separated pak numbers (default: all)")
    ap.add_argument("--backup", help="move originals here")
    ap.add_argument("--scan", action="store_true", help="dry run")
    ap.add_argument("--only", help="regex: only touch asset paths matching this (e.g. /stage/(ind|kzk)/)")
    ap.add_argument("--shaders", action="store_true",
                    help="also strip the unused DirectX 10 (SM4) shader copies from materials")
    a = ap.parse_args()
    aes = cipher()
    paks = [n for n in sorted(os.listdir(S.PAKS), key=S._pak_num) if n.endswith(".pak")]
    if a.paks:
        want = {int(x) for x in a.paks.split(",")}
        paks = [n for n in paks if S._pak_num(n) in want]
    caps = {"default": a.max}
    caps.update({t: getattr(a, t) for t in TEX_TYPES + ("stage",) if getattr(a, t)})
    print("Caps:", caps)
    total = sum(optimize_pak(aes, p, caps, a.backup, a.scan, a.shaders, re.compile(a.only, re.I) if a.only else None) for p in paks)
    print(f"\n{total} files {'to process' if a.scan else 'rewritten'}.")


if __name__ == "__main__":
    main()
