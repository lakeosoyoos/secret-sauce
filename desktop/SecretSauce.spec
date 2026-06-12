# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SecretSauce desktop app.
# Build:  pyinstaller SecretSauce.spec   (run ON Windows to make the .exe)
#
# Produces a one-FOLDER bundle (dist/SecretSauce/) — more reliable than
# one-file for a Streamlit + numpy/scipy/matplotlib app, and faster to start.
# Zip the dist/SecretSauce folder to hand to a tech; they extract and run
# SecretSauce.exe inside it.

from PyInstaller.utils.hooks import (collect_all, collect_submodules,
                                     collect_data_files)

datas, binaries, hiddenimports = [], [], []

# Pull in everything these packages need (data files, dylibs, submodules).
for pkg in ["streamlit", "altair", "pyarrow", "numpy", "scipy",
            "matplotlib", "openpyxl", "pandas"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Our own modules + assets — must travel with the bundle.
datas += [
    ("desktop_app.py", "."),
    ("report.py", "."),
    ("report_sor.py", "."),
    ("sor_reader324802a.py", "."),
    ("trc_parser.py", "."),
    ("zerodblogo.png", "."),
]
hiddenimports += ["report", "report_sor", "sor_reader324802a", "trc_parser",
                  "tkinter", "tkinter.filedialog"]

# pkg_resources (pulled in by Streamlit) vendors several packages that
# PyInstaller's runtime hook can fail to bundle, giving e.g.
# "the jaraco package is required" at launch. Bundle them three ways so it
# cannot slip through:
#   1) the whole pkg_resources/setuptools tree, INCLUDING its _vendor copies
#      (jaraco.*, packaging, platformdirs...) — what the extern importer reaches
#      for first;
#   2) the same packages installed top-level (requirements-desktop.txt) as the
#      extern importer's fallback;
#   3) explicit hiddenimports as a final belt-and-suspenders.
# The CI boot self-test actually launches the .exe and fails the build if any of
# this is still wrong, so a packaging gap can never reach a tech.
hiddenimports += collect_submodules("pkg_resources")
hiddenimports += collect_submodules("setuptools")
datas += collect_data_files("pkg_resources")
for _pkg in ["jaraco", "jaraco.text", "jaraco.functools", "jaraco.context",
             "more_itertools", "packaging", "platformdirs", "appdirs",
             "ordered_set"]:
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        pass   # not installed top-level — the vendored copy (1) still covers it
hiddenimports += ["appdirs", "jaraco.text", "jaraco.functools",
                  "jaraco.context", "more_itertools", "packaging",
                  "platformdirs", "pkg_resources"]

block_cipher = None

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # WeasyPrint needs native GTK/Pango libs that don't bundle cleanly on
    # Windows. We default to Excel output, so exclude it; PDF still works
    # if Chrome is installed (report.py falls back to headless Chrome).
    excludes=["weasyprint"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SecretSauce",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # no black console window — silent double-click app.
                           # launcher.py redirects output to a log file, and the
                           # app has a Quit button (there's no console to close).
    disable_windowed_traceback=False,
    icon=None,             # drop a SecretSauce.ico here and set its path to brand it
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SecretSauce",
)
