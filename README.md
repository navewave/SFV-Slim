# SFV-Slim

Shrinks a Steam install of **Street Fighter V** for versus and online play (tested with Polarity mod)

A typical result is about **46 GB → 10–20 GB**, depending on what you pick.

This tool contains no game files. It rewrites your own install.

## Use

1. Download `SFV-Slim.exe` from [Releases](../../releases) (no Python needed).
2. Put the exe into the `StreetFighterV` game folder.
3. Close the game and run `SFV-Slim.exe`. It asks a few questions, shows a summary, and changes nothing until you confirm.

Windows may say it "protected your PC", because the exe isn't signed. Click **More info → Run anyway**.

## What it asks

- **Remove single-player extras and shop media?** Character Story, intro movies, Gallery, Story/Arcade extras, shop voices and item images. Versus, Training, online and the shop itself still work.
- **Remove background music?** Turn music off in Options afterwards.
- **Costumes:** keep all, or default costumes only (you pick who keeps alternates).
- **Stages:** keep all, or Training only.
- **Texture quality** for characters and stages: 1024 (recommended), 512 (smallest), or leave alone.

Unused DirectX 10 shader copies are always removed when the key is present. The game requires a DirectX 11 card, so they are never loaded (about 2.2 GB, no visual change).

## Undo

Steam → Street Fighter V → Properties → Installed Files → **Verify integrity of game files**. Then run SFV-Slim again if you want different choices.

## Run from source

Needs Python 3, plus `python -m pip install --user pycryptodome numpy` for texture shrinking. Put these files in the `SFV-Slim` folder and run `SFV-Slim.bat` (or `python wizard.py`). `build_exe.bat` builds the exe (also needs `pyinstaller`).
