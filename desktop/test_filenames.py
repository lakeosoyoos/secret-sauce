"""Filename -> fiber-number regression sweep for Secret Sauce.

Locks in the four EXFO filename bug patterns hardened in report._extract_fiber_num
(sibling of splice-report commit 05eabe2):
  1. trailing space before the extension  (DURSAN001_1550 .json)
  2. multi-wavelength concatenated suffix  (VERSLK001_131015501625 .json)
  3. macOS AppleDouble sidecars            (._STRROM0001_1550.sor)
  4. multi-cable name collisions           (FTHNTXAD01_AD04 + _AD05 -> same id)

Run:  python test_filenames.py   (exits nonzero on any regression).
Wired into the Windows CI so a regression fails the build before it publishes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import _extract_fiber_num, _otdr_paths, _warn_name_collisions

# (filename, expected fiber number)  — from the 38k-filename survey + SS data.
EXPECTED = [
    ("LAGDUR0001.sor", 1),
    ("DURLAG0001.sor", 1),
    ("Norsea001_1550.sor", 1),
    ("Norsea432_1550.sor", 432),
    ("Seattle to Spokane d.0431.sor", 431),
    ("Seattle-Stevens-d.0001.sor", 1),
    ("20260520_LAGDUR0001.sor", 1),
    ("fiber 17.json", 17),
    ("DURSAN001_1550 .json", 1),           # pattern 1: trailing space
    ("ELMMIL1152_1550 .json", 1152),       # pattern 1
    ("SANDUR864_1550 .json", 864),         # pattern 1
    ("VERSLK001_131015501625 .json", 1),   # pattern 2: multi-lambda + space
    ("VERSLK018_131015501625.trc", 18),    # pattern 2
    ("TEST0001_155016251310.trc", 1),      # pattern 2
    ("ELMMILsh0001_1550.sor", 1),
    ("shortTUCROM445_1550.sor", 445),
    ("DNW1DNW50007withstartstop.sor", 50007),
    ("TrimmedCHM1CHM20001.sor", 20001),
    ("CHC-HCH-LS-089.trc", 89),
    ("DNWRCH-A-271.sor", 271),
    ("._STRROM0001_1550.sor", None),       # pattern 3: AppleDouble -> skipped
    ("._DURSAN001_1550 .json", None),      # pattern 3
    ("LAGDUR.sor", None),                  # no digits
    ("Norsea_1550.sor", None),             # wavelength only
    # ---- Secret Sauce canonical data (must stay stable) ----
    ("TUCROM453_1550.sor", 453),           # Beta ROMERO<->TUCUMCARI
    ("DURANCsh001_1550.sor", 1),           # Duran->Ancho FEC panel
    ("ANCDURsh864_1550.sor", 864),         # Ancho->Duran FEC panel
    ("ELMMIL0001_1550 .json", 1),          # ELMMIL 1152
]


def main():
    fails = []
    for fn, exp in EXPECTED:
        got = _extract_fiber_num(fn)
        if got != exp:
            fails.append(f"  {fn!r}: got {got!r}, expected {exp!r}")

    # Pattern 3 — _otdr_paths must skip AppleDouble sidecars on disk.
    import tempfile
    td = tempfile.mkdtemp()
    for nm in ("REAL0001_1550.sor", "._REAL0001_1550.sor", "._junk.sor"):
        open(os.path.join(td, nm), "w").close()
    paths = _otdr_paths(td, "*.sor")
    if any(os.path.basename(p).startswith("._") for p in paths):
        fails.append("  _otdr_paths did NOT skip AppleDouble ._* files")
    if len(paths) != 1:
        fails.append(f"  _otdr_paths returned {len(paths)} paths, expected 1")

    # Pattern 4 — name collision keeps the first, skips the second.
    kept = _warn_name_collisions([
        {"name": "FTHNTXAD01", "filepath": "FTHNTXAD01_AD04_001.json"},
        {"name": "FTHNTXAD01", "filepath": "FTHNTXAD01_AD05_001.json"},
        {"name": "OTHER",      "filepath": "OTHER_001.json"},
    ])
    if len(kept) != 2:
        fails.append(f"  _warn_name_collisions kept {len(kept)}, expected 2")

    if fails:
        print("FILENAME SWEEP FAILED:")
        print("\n".join(fails))
        sys.exit(1)
    print(f"FILENAME SWEEP PASSED — {len(EXPECTED)} extractor cases "
          f"+ AppleDouble skip + collision skip")


if __name__ == "__main__":
    main()
