"""SFV-Slim all-in-one: asks a few questions, then slims and optimizes the game in one go.

Removal steps work on their own. Texture/shader optimization additionally needs
aes_key.txt (the game's pak key, which you supply) and pycryptodome + numpy.
"""
import os
import sys

import sfv_slim as S

HERE = S.HERE
KEY_FILE = os.path.join(HERE, "aes_key.txt")
SKIP = 1 << 20  # a "cap" no texture reaches = leave alone


def yes(prompt, default):
    d = "Y/n" if default else "y/N"
    print(prompt)
    a = S.ask(f"  [{d}]: ", "y" if default else "n").lower()
    return a.startswith("y")


def choice(prompt, options, default=1):
    print(prompt)
    for i, (label, _) in enumerate(options, 1):
        print(f"   {i}) {label}")
    while True:
        a = S.ask(f"  Choose 1-{len(options)} [{default}]: ", str(default))
        if a.isdigit() and 1 <= int(a) <= len(options):
            return options[int(a) - 1][1]
        print("  Please type one of the numbers.")


def optimizer_ready():
    missing = []
    if not os.path.exists(KEY_FILE):
        missing.append("aes_key.txt (the game's pak key) next to this script")
    for mod, pip in (("Crypto", "pycryptodome"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(f"python package {pip}  (python -m pip install --user {pip})")
    return missing


def main():
    if not os.path.isdir(S.PAKS):
        sys.exit(f"Can't find {S.PAKS}. Put the SFV-Slim folder inside the StreetFighterV game folder.")
    print("=" * 70)
    print(" SFV-Slim - shrink Street Fighter V for versus / online play")
    print("=" * 70)
    size = sum(os.path.getsize(os.path.join(S.PAKS, n)) for n in os.listdir(S.PAKS) if n.endswith(".pak"))
    print(f"Game data right now: {S.human(size)}\n")
    print("Answer a few questions; nothing changes until you confirm at the end.")
    print("Undo everything any time with Steam > Properties > Installed Files >")
    print("'Verify integrity of game files'.\n")

    tiers = set()
    keep = set()

    print("--- Content to remove ---------------------------------------------")
    if yes("Remove single-player extras and shop media (Character Story, intro movies,\n"
           "Gallery, Story/Arcade extras, shop voices and item images)?\n"
           "Versus, Training, online and the shop itself are unaffected.", True):
        tiers |= {1, 2, 3, 6}
    if yes("\nRemove all background music? (Turn music off in Options afterwards.)", False):
        tiers.add(7)
    if choice("\nCostumes:", [("Keep every costume", False),
                              ("Default costumes only (you pick who keeps alternates)", True)]):
        tiers.add(4)
        keep = S.choose_costume_keepers()
    if choice("\nStages:", [("Keep every stage", False), ("Training stage only", True)]):
        tiers.add(5)

    print("\n--- Texture optimization (keeps everything, just smaller) ----------")
    caps, shaders = None, False
    missing = optimizer_ready()
    if missing:
        print("Skipped - needs:")
        for m in missing:
            print("   - " + m)
    else:
        q = choice("Texture quality (characters and stages):", [
            ("1024 (recommended)", 1024),
            ("512  (smallest)", 512),
            ("Leave textures alone", None)])
        # Unused DirectX 10 shader copies are always removed: the game needs a DX11 card,
        # so they are never loaded (~2.2 GB, no visual change).
        shaders = True
        caps = {"default": q or SKIP}
        if q:
            caps["sss"] = min(512, q)
            caps["stage"] = q

    print("\n--- Summary ---------------------------------------------------------")
    if not tiers and not caps:
        print("Nothing selected. Bye!")
        return
    for t in sorted(tiers):
        extra = ""
        if t == 4:
            extra = "  (keeping alternates for: " + (", ".join(sorted(S.CHARS[c] for c in keep)) or "nobody") + ")"
        print(f"  remove   {S.TIER_NAMES[t]}{extra}")
    if caps:
        if caps["default"] != SKIP:
            print(f"  shrink   costume textures to {caps['default']} (SSS {caps['sss']})")
        if caps.get("stage"):
            print(f"  shrink   stage textures to {caps['stage']}")
        if shaders:
            print("  strip    unused DirectX 10 shaders")
    if tiers:
        print("\nSpace freed by removals:")
        S.cmd_scan(tiers, keep)
    if caps:
        print("\nOptimization typically frees another 15-20 GB on a stock install "
              "(less if already done).")

    backup = None  # undo = Steam 'Verify integrity of game files'
    if not yes("\nClose the game first. Start now?", False):
        print("Cancelled, nothing changed.")
        return

    if tiers:
        print("\n=== Removing content ===")
        S.cmd_apply(tiers, backup, keep)
    if caps:
        print("\n=== Optimizing textures/shaders (this can take 10-20 minutes) ===")
        import optimize as O
        aes = O.cipher()
        paks = [n for n in sorted(os.listdir(S.PAKS), key=S._pak_num) if n.endswith(".pak")]
        n = sum(O.optimize_pak(aes, p, caps, backup, False, shaders) for p in paks)
        print(f"{n} files optimized.")
    print("\n=== Checking all paks ===")
    bad = S.cmd_verify()
    if not bad:
        size2 = sum(os.path.getsize(os.path.join(S.PAKS, n)) for n in os.listdir(S.PAKS) if n.endswith(".pak"))
        print(f"\nGame data: {S.human(size)} -> {S.human(size2)}")
        print("\nAll done! Enjoy. Undo any time with Steam 'Verify integrity of game files'.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
    except SystemExit as ex:
        if ex.code not in (None, 0):
            print(ex.code if isinstance(ex.code, str) else f"Stopped (code {ex.code}).")
    except Exception:
        import traceback
        traceback.print_exc()
        print("\nSomething went wrong (details above). Paks are only swapped after they verify,")
        print("so the game files are either unchanged or fully updated.")
    if S.FROZEN:
        S.ask("\nPress Enter to close.")
