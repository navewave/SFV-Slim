"""
SFV-Slim - shrink Street Fighter V (Steam) by stripping single-player-only media
from the base game paks. Meant for netplay/versus-only setups.

The base paks are UE4 pak v3. Their file data is AES-encrypted but the index
isn't, so entries can be copied byte-for-byte and re-indexed without the key.

Usage (from this folder). For the guided wizard, run SFV-Slim.bat or wizard.py instead:
    python sfv_slim.py lite                          # interactive Lite (asks which costumes to keep)
    python sfv_slim.py scan    [--tiers 1,2,3]      # dry run: what would be removed
    python sfv_slim.py apply   [--tiers 1,2,3] [--backup DIR]
    python sfv_slim.py verify                        # re-parse every pak, check index hashes
    python sfv_slim.py status

Undo: Steam > Street Fighter V > Properties > Installed Files > Verify integrity.
(or copy back the originals if you used --backup)
"""
import argparse, hashlib, json, os, re, shutil, struct, sys, time

FROZEN = getattr(sys, "frozen", False)  # running as SFV-Slim.exe (PyInstaller)
# folder holding the script/exe (where aes_key.txt, backups etc. live)
HERE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
# bundled read-only resources (stub movie) live in the exe's unpack folder
RES = getattr(sys, "_MEIPASS", HERE)
# works from an SFV-Slim folder inside the game folder, or directly in the game folder
if os.path.isdir(os.path.join(HERE, "StreetFighterV", "Content", "Paks")):
    GAME = HERE
else:
    GAME = os.path.abspath(os.path.join(HERE, ".."))
PAKS = os.path.join(GAME, "StreetFighterV", "Content", "Paks")
MOVIES = os.path.join(GAME, "StreetFighterV", "Content", "Movies")
STATE = os.path.join(GAME, "sfv_slim_state.json")
STUB_MOVIE = os.path.join(RES, "stub", "stub.wmv")

PAK_MAGIC = 0x5A6F12E1

# ---------------------------------------------------------------- drop rules
# Rules match the full virtual path, lower-cased, '/'-separated, e.g.
# "streetfighterv/content/chara/ryu/illustdemo/image/ryu_001_001.uasset".
# Only heavy media (textures, sound, cinematic maps) is dropped; small data
# assets (DA_*, tables) are kept so menus still find their definitions.

TIER1 = {  # Character Story stills + voice (pakchunk53 is 100% this)
    "pakchunk53-WindowsNoEditor.pak": [r"/chara/[^/]+/illustdemo/(image|sound)/"],
}

TIER3_RULES = [
    r"/chara/[^/]+/illustdemo/(image|sound)/",       # leftover character-story media
    r"/gamemode/gallery/illust/",                    # Gallery full-size art
    r"/sound/bgm/gallery/",                          # Gallery music
    r"/gamemode/mode03/image/gallery/illust/",       # Arcade-mode gallery art (thumbs kept)
    r"/content/demo/story/",                         # "A Shadow Falls" cinematics
    r"/winsysasset/story/general/image/",            # Story-mode cut-scene stills
    r"/gamemode/arcade/mode\d+/sound/",              # Arcade-mode era BGM
    r"/sound/bgm/bgm_credit",                        # Credits music
]
TIER3 = {p: TIER3_RULES for p in (
    "pakchunk0-WindowsNoEditor.pak",
    "pakchunk67-WindowsNoEditor.pak",
    "pakchunk68-WindowsNoEditor.pak",
)}

TIER2_MOVIES = ["OpeningTrPC.wmv", "AE_OpeningTrPC.wmv"]

# Tier 4: alternate costumes. Tested 2026-11: a missing costume disappears from the
# select screen, and an opponent's removed costume shows up as the default online.
# Default costume (01), colour sets (31-37), 0C/0E and Common are always kept.
CHARS = {  # internal code -> name (playable characters only)
    "Z23": "Abigail", "Z41": "Akira", "Z20": "Akuma", "ALX": "Alex", "BSN": "Balrog (boxer)",
    "BRD": "Birdie", "Z27": "Blanka", "CMY": "Cammy", "CNL": "Chun-Li", "Z29": "Cody",
    "Z38": "Dan", "DSM": "Dhalsim", "Z33": "E. Honda", "Z22": "Ed", "FAN": "F.A.N.G",
    "Z28": "Falke", "Z30": "G", "Z36": "Gill", "GUL": "Guile", "IBK": "Ibuki",
    "JRI": "Juri", "Z32": "Kage", "KRN": "Karin", "KEN": "Ken", "Z21": "Kolin",
    "LAR": "Laura", "Z34": "Lucia", "Z42": "Luke", "VEG": "M. Bison", "Z24": "Menat",
    "NSH": "Nash", "NCL": "Necalli", "Z40": "Oro", "Z35": "Poison", "RMK": "R. Mika",
    "RSD": "Rashid", "Z39": "Rose", "RYU": "Ryu", "Z31": "Sagat", "Z26": "Sakura",
    "Z37": "Seth", "URN": "Urien", "BLR": "Vega (claw)", "ZGF": "Zangief", "Z25": "Zeku",
}
ALT_COSTUME = r"(0[2-9]|[12]\d|30)"

# Tier 5: every battle stage except Training. Stages are client-side in netplay and a
# missing stage simply disappears from stage select (tested 2026-11).
# Kept: TRN + B00 (training-stage candidates), CMN (shared stage assets), BLK, RND,
# and all menu maps (Title, CharacterSelect, Entry, Preview, SPN, SZK).
STAGE_CODES = (
    "afb bo1 bo2 bo3 bo4 bo5 bos br1 brz chn dbi doj in1 ind kpb kz1 kz2 kzk ld1 ldn lvs "
    "ny1 ny2 ny3 nyk nz1 nz2 nzl ru1 ru2 rus s01 s02 s03 s04 s05 s06 s07 s08 s09 s11 s12 "
    "s13 s14 s15 s16 s17 s18 s19 s20 s21 s22 s23 s24 s25 sh1 sh2 sh3 sh4 sh5 sha sx1 sx2 tai"
).split()
_ST = "|".join(STAGE_CODES)
TIER5_RULES = [
    rf"/content/stage/({_ST})/",
    rf"/content/maps/({_ST})(\.umap$|/)",
    rf"/content/maps/reduced/({_ST})_reduced",
]

# Tier 6: shop leftovers (the shop doesn't work without Capcom's servers anyway)
TIER6_RULES = [
    r"/chara/[^/]+/sound/shop/",            # costume/character preview voices
    r"/gamemode/shop/shopvoice/",
    r"/winsysasset/shop/shopitemimage/",
]

# Tier 7: background music (SoundAtomCueSheet assets with "bgm" in the name).
# Leaves the small BGM data assets (da_*) alone so menus/settings still load.
TIER7_RULES = [
    r"/sound/(?:[^/]+/)*(?!da_)[^/]*bgm[^/]*\.uasset$",
]

TIER_NAMES = {
    1: "Character Story stills + voice",
    2: "Intro movies",
    3: "Gallery / Story mode / Arcade extras",
    4: "Alternate costumes",
    5: "All stages except Training",
    6: "Shop voices + item images",
    7: "All background music (turn music off in Options)",
}
LITE = {1, 2, 3, 4, 5, 6}


def costume_rule(keep_codes):
    drop = [c.lower() for c in CHARS if c not in keep_codes]
    if not drop:
        return []
    return [rf"/chara/({'|'.join(drop)})/skelmesh/{ALT_COSTUME}/"]


def rules_for(tiers, keep_codes=()):
    """-> {pak filename: [compiled regex]} for every pak in the Paks folder."""
    every = []
    if 4 in tiers:
        every += costume_rule(set(keep_codes))
    if 5 in tiers:
        every += TIER5_RULES
    if 6 in tiers:
        every += TIER6_RULES
    if 7 in tiers:
        every += TIER7_RULES
    out = {}
    for pak in sorted(os.listdir(PAKS), key=_pak_num):
        if not pak.endswith(".pak"):
            continue
        rs = list(every)
        if 1 in tiers:
            rs += TIER1.get(pak, [])
        if 3 in tiers:
            rs += TIER3.get(pak, [])
        if rs:
            out[pak] = [re.compile(r) for r in rs]
    return out


def _pak_num(n):
    m = re.search(r"chunk(\d+)", n)
    return int(m.group(1)) if m else 999


def costume_sizes():
    """alt-costume bytes per character code, from the current paks."""
    rx = re.compile(rf"/chara/([^/]+)/skelmesh/{ALT_COSTUME}/")
    sizes = {c: 0 for c in CHARS}
    for pak in os.listdir(PAKS):
        if pak.endswith(".pak"):
            mount, ents, _ = read_pak(os.path.join(PAKS, pak))
            for e in ents:
                m = rx.search(full_path(mount, e.name))
                if m and m.group(1).upper() in sizes:
                    sizes[m.group(1).upper()] += e.size
    return sizes


# ---------------------------------------------------------------- pak format
def _rstr(f):
    n = struct.unpack("<i", f.read(4))[0]
    if n < 0:
        return f.read(-n * 2).decode("utf-16-le").rstrip("\x00")
    return f.read(n).decode("latin-1").rstrip("\x00")


def _wstr(s):
    try:
        b = s.encode("ascii") + b"\x00"
        return struct.pack("<i", len(b)) + b
    except UnicodeEncodeError:
        b = s.encode("utf-16-le") + b"\x00\x00"
        return struct.pack("<i", -(len(b) // 2)) + b


class Entry:
    __slots__ = ("name", "offset", "size", "usize", "comp", "sha1", "blocks", "enc", "bsize")

    def header(self, offset_field):
        b = struct.pack("<qqqi", offset_field, self.size, self.usize, self.comp) + self.sha1
        if self.comp:
            b += struct.pack("<i", len(self.blocks))
            for s, e in self.blocks:
                b += struct.pack("<qq", s, e)
        b += struct.pack("<BI", self.enc, self.bsize)
        return b


def read_pak(path):
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        f.seek(-44, 2)
        magic, ver, idx_off, idx_size = struct.unpack("<IiQQ", f.read(24))
        idx_hash = f.read(20)
        if magic != PAK_MAGIC or ver != 3:
            raise ValueError(f"{path}: not a v3 pak (magic {magic:#x}, ver {ver})")
        if idx_off + idx_size + 44 != size:
            raise ValueError(f"{path}: unexpected footer layout")
        f.seek(idx_off)
        raw = f.read(idx_size)
        if hashlib.sha1(raw).digest() != idx_hash:
            raise ValueError(f"{path}: index hash mismatch")
        f.seek(idx_off)
        mount = _rstr(f)
        count = struct.unpack("<i", f.read(4))[0]
        ents = []
        for _ in range(count):
            e = Entry()
            e.name = _rstr(f)
            e.offset, e.size, e.usize, e.comp = struct.unpack("<qqqi", f.read(28))
            e.sha1 = f.read(20)
            e.blocks = []
            if e.comp:
                nb = struct.unpack("<i", f.read(4))[0]
                e.blocks = [struct.unpack("<qq", f.read(16)) for _ in range(nb)]
            e.enc, e.bsize = struct.unpack("<BI", f.read(5))
            ents.append(e)
        return mount, ents, idx_off


def full_path(mount, name):
    p = (mount.replace("../../../", "") + name).replace("\\", "/")
    return "/" + p.lower()


def plan_pak(path, rules):
    mount, ents, idx_off = read_pak(path)
    # region of each entry = [offset, next entry offset) -> includes inline header + AES padding
    by_off = sorted({e.offset for e in ents})
    nxt = {o: (by_off[i + 1] if i + 1 < len(by_off) else idx_off) for i, o in enumerate(by_off)}
    dropping = {id(e) for e in ents if any(r.search(full_path(mount, e.name)) for r in rules)}
    # never drop a region still referenced by a kept entry (shared offsets)
    kept_offs = {e.offset for e in ents if id(e) not in dropping}
    drop = [e for e in ents if id(e) in dropping and e.offset not in kept_offs]
    dropped = {id(e) for e in drop}
    keep = [e for e in ents if id(e) not in dropped]  # original index order preserved
    return mount, ents, keep, drop, nxt, idx_off


def region_bytes(e, nxt):
    return nxt[e.offset] - e.offset


def write_pak(src, dst, mount, keep, nxt):
    moved = {}  # old offset -> new offset (shared regions written once)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        # data section: copy each kept region in original file order
        for e in sorted(keep, key=lambda e: e.offset):
            if e.offset in moved:
                continue
            new_off = fo.tell()
            fi.seek(e.offset)
            hlen = len(e.header(0))
            if fi.read(hlen) != e.header(0):
                raise ValueError(f"inline header mismatch for {e.name}")
            # inline header: offset field stays 0, compression block offsets are absolute -> shift
            fo.write(_shift(e, new_off - e.offset, new_off).header(0))
            remaining = region_bytes(e, nxt) - hlen
            while remaining:
                chunk = fi.read(min(remaining, 8 << 20))
                if not chunk:
                    raise IOError("unexpected EOF")
                fo.write(chunk)
                remaining -= len(chunk)
            moved[e.offset] = new_off
        # index: original entry order
        idx = _wstr(mount) + struct.pack("<i", len(keep))
        parts = []
        for e in keep:
            ne = _shift(e, moved[e.offset] - e.offset, moved[e.offset])
            parts.append(_wstr(ne.name) + ne.header(ne.offset))
        idx += b"".join(parts)
        idx_off = fo.tell()
        fo.write(idx)
        fo.write(struct.pack("<IiQQ", PAK_MAGIC, 3, idx_off, len(idx)) + hashlib.sha1(idx).digest())


def _shift(e, delta, new_off):
    ne = Entry()
    for s in Entry.__slots__:
        setattr(ne, s, getattr(e, s))
    ne.offset = new_off
    ne.blocks = [(s + delta, t + delta) for s, t in e.blocks]
    return ne


# ---------------------------------------------------------------- commands
def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"applied": {}}


def save_state(st):
    json.dump(st, open(STATE, "w"), indent=2)


def human(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def cmd_scan(tiers, keep_codes=()):
    total = 0
    for pak, rules in rules_for(tiers, keep_codes).items():
        mount, ents, keep, drop, nxt, _ = plan_pak(os.path.join(PAKS, pak), rules)
        if not drop:
            continue
        saved = sum(region_bytes(e, nxt) for e in drop)
        total += saved
        print(f"  {pak:34} drop {len(drop):6} of {len(ents):6} files  -> {human(saved)}")
    if 2 in tiers:
        for m in TIER2_MOVIES:
            p = os.path.join(MOVIES, m)
            if os.path.exists(p):
                s = max(os.path.getsize(p) - os.path.getsize(STUB_MOVIE), 0)
                if s:
                    total += s
                    print(f"  {m:34} replace with 1s stub         -> {human(s)}")
    print(f"\n  Total to free: {human(total)}")
    return total


def cmd_apply(tiers, backup, keep_codes=()):
    st = load_state()
    if backup:
        os.makedirs(backup, exist_ok=True)
    for pak, rules in rules_for(tiers, keep_codes).items():
        path = os.path.join(PAKS, pak)
        mount, ents, keep, drop, nxt, _ = plan_pak(path, rules)
        if not drop:
            continue
        before = os.path.getsize(path)
        print(f"{pak}: dropping {len(drop)} files, rewriting {human(before)} ...", flush=True)
        t = time.time()
        tmp = path + ".slim.tmp"
        write_pak(path, tmp, mount, keep, nxt)
        m2, e2, _ = read_pak(tmp)  # verify the new file before swapping it in
        if len(e2) != len(keep) or m2 != mount:
            os.remove(tmp)
            sys.exit(f"Verification of rebuilt {pak} failed; original left untouched.")
        if backup:
            shutil.move(path, os.path.join(backup, pak))
        os.replace(tmp, path)
        after = os.path.getsize(path)
        print(f"  done in {time.time() - t:.0f}s: {human(before)} -> {human(after)}")
        prev = st["applied"].get(pak, {})
        st["applied"][pak] = {"before": prev.get("before", before), "after": after,
                              "tiers": sorted(set(prev.get("tiers", [])) | set(tiers)),
                              "time": time.ctime()}
        save_state(st)
    if 2 in tiers:
        for m in TIER2_MOVIES:
            p = os.path.join(MOVIES, m)
            if not os.path.exists(p) or os.path.getsize(p) == os.path.getsize(STUB_MOVIE):
                continue
            before = os.path.getsize(p)
            if backup:
                shutil.move(p, os.path.join(backup, m))
            shutil.copyfile(STUB_MOVIE, p)
            print(f"{m}: replaced with stub ({human(before)} freed)")
            st["applied"][m] = {"before": before, "after": os.path.getsize(p), "time": time.ctime()}
            save_state(st)
    if 4 in tiers:
        st["kept_costumes"] = sorted(keep_codes)
        save_state(st)
    print("\nAll done. Undo any time with Steam 'Verify integrity of game files'.")


def cmd_verify():
    bad = 0
    for n in sorted(os.listdir(PAKS), key=_pak_num):
        if n.endswith(".pak"):
            try:
                _, ents, _ = read_pak(os.path.join(PAKS, n))
            except Exception as ex:  # noqa
                print("BAD ", n, ex)
                bad += 1
                continue
            print("ok  ", n, len(ents), "files")
    print("all paks OK" if not bad else f"{bad} bad pak(s)")
    return bad


def cmd_status():
    st = load_state()
    if not st["applied"]:
        print("No SFV-Slim changes recorded.")
    freed = 0
    for k, v in st["applied"].items():
        freed += v["before"] - v["after"]
        print(f"{k:34} {human(v['before'])} -> {human(v['after'])}  ({v['time']})")
    if freed:
        print(f"\nFreed: {human(freed)}")
    if st.get("kept_costumes") is not None:
        names = [CHARS.get(c, c) for c in st["kept_costumes"]]
        print("Alternate costumes kept for: " + (", ".join(names) if names else "nobody"))


# ---------------------------------------------------------------- interactive
def ask(prompt, default=""):
    try:
        a = input(prompt).strip()
    except EOFError:
        a = ""
    return a or default


def parse_keep(text, order):
    """'3 12, 40' (numbers from the list) or 'Ryu, Chun-Li' (names/codes) -> set of codes."""
    by_name = {v.lower(): k for k, v in CHARS.items()}
    keep = set()
    for tok in [t.strip() for t in re.split(r"[,;]|\s(?=\d)|(?<=\d)\s", text) if t.strip()]:
        if tok.isdigit():
            n = int(tok)
            if not 1 <= n <= len(order):
                raise ValueError(f"{n} isn't on the list (1-{len(order)})")
            keep.add(order[n - 1])
        elif tok.upper() in CHARS:
            keep.add(tok.upper())
        elif tok.lower() in by_name:
            keep.add(by_name[tok.lower()])
        else:
            raise ValueError(f"don't know '{tok}'")
    return keep


def list_order():
    return sorted(CHARS, key=lambda c: CHARS[c].lower())


def choose_costume_keepers():
    print("\nReading costume sizes...")
    sizes = costume_sizes()
    order = list_order()
    print("\nAlternate costumes will be removed for every character (default costumes always stay).")
    print("Keep alternate costumes for anyone?\n")
    cells = []
    for i, c in enumerate(order, 1):
        sz = f"{sizes[c] / 1e6:4.0f} MB" if sizes[c] >= 1e6 else "  removed"
        cells.append(f"{i:2}) {CHARS[c]:15} {sz}")
    rows = (len(cells) + 2) // 3
    for r in range(rows):
        print("   ".join(cells[r + k * rows] for k in range(3) if r + k * rows < len(cells)))
    print("\n('removed' = already gone; get them back with Steam 'Verify integrity', then re-run.)")
    while True:
        txt = ask("\nNumbers to KEEP, separated by spaces (e.g. 4 19 38), or just press Enter for none: ")
        try:
            keep = parse_keep(txt, order)
        except ValueError as ex:
            print(f"  {ex} - try again.")
            continue
        if keep:
            print("  Keeping alternates for: " + ", ".join(sorted(CHARS[c] for c in keep)))
        else:
            print("  Keeping no alternate costumes.")
        if ask("  Correct? [Y/n] ", "y").lower().startswith("y"):
            return keep


def run_profile(tiers, keep=None, backup=None, yes=False):
    if 4 in tiers and keep is None:
        keep = choose_costume_keepers()
    keep = keep or set()
    print("\nThis will remove:")
    for t in sorted(tiers):
        print(f"  [{t}] {TIER_NAMES[t]}")
    print("\nCalculating...\n")
    if cmd_scan(tiers, keep) == 0:
        print("Nothing left to remove - already applied.")
        return
    if not yes:
        if backup is None:
            b = ask("\nKeep a backup of the original files in SFV-Slim\\_backup so you can undo instantly?\n"
                    "(Needs no extra space, but the space is only freed once you delete that folder.) [y/N] ", "n")
            backup = os.path.join(HERE, "_backup") if b.lower().startswith("y") else None
        if not ask("\nClose the game and Pak Mod Manager first. Proceed? [y/N] ", "n").lower().startswith("y"):
            print("Cancelled, nothing changed.")
            return
    cmd_apply(tiers, backup, keep)


def menu():
    print("SFV-Slim - shrink Street Fighter V for versus/online play\n")
    print("  1) LITE - strip everything: default costumes only, Training stage only, no single-player media")
    print("  2) Custom - pick tiers yourself")
    print("  3) Remove music - delete all background music (for players with music off)")
    print("  4) Status - show what's been done")
    print("  5) Verify - check all paks are healthy")
    print("  q) Quit")
    c = ask("\nChoose: ", "q").lower()
    if c == "1":
        run_profile(LITE)
    elif c == "2":
        print()
        for t, n in TIER_NAMES.items():
            print(f"  {t}) {n}")
        txt = ask("\nTiers to apply (e.g. 1 2 3): ")
        tiers = {int(x) for x in re.split(r"[,\s]+", txt) if x.isdigit() and int(x) in TIER_NAMES}
        if tiers:
            run_profile(tiers)
    elif c == "3":
        run_profile({7})
    elif c == "4":
        cmd_status()
    elif c == "5":
        cmd_verify()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["lite", "music", "scan", "apply", "verify", "status"])
    ap.add_argument("--tiers", default="1,2,3", help="for scan/apply, e.g. 1,2,3,4,5,6")
    ap.add_argument("--keep", default=None,
                    help='characters whose alternate costumes to keep, e.g. "Ryu, Chun-Li" ("" = none)')
    ap.add_argument("--backup", help="move originals here instead of discarding them")
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    a = ap.parse_args()
    if not os.path.isdir(PAKS):
        sys.exit(f"Can't find {PAKS}. Put the SFV-Slim folder inside the StreetFighterV game folder.")
    keep = None if a.keep is None else parse_keep(a.keep, list_order())
    tiers = {int(x) for x in a.tiers.split(",") if x.strip()}
    if a.command is None:
        menu()
    elif a.command == "lite":
        run_profile(LITE, keep, a.backup, a.yes)
    elif a.command == "music":
        run_profile({7}, None, a.backup, a.yes)
    elif a.command == "scan":
        cmd_scan(tiers, keep or set())
    elif a.command == "apply":
        run_profile(tiers, keep, a.backup, a.yes)
    elif a.command == "verify":
        sys.exit(1 if cmd_verify() else 0)
    else:
        cmd_status()


if __name__ == "__main__":
    main()
