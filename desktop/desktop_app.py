"""
Secret Sauce — Desktop (local) edition.

Runs entirely on this computer. Pick a folder of OTDR files (.sor / .trc /
.json); the report is written next to your files. No upload, no cloud, no
file-size limit — it uses this machine's memory, so the big Platteville-size
runs that crashed the website work fine here.

Dev run:  streamlit run desktop_app.py
Packaged: launched by launcher.py inside SecretSauce.exe (see build kit).
"""
import os
import sys
import glob
import shutil
import tempfile
from collections import defaultdict

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from report import (run_json_xlsx_bytes, run_trc_xlsx_bytes,
                    run_json_bytes, run_trc_bytes)
from report_sor import run_sor_xlsx_bytes, run_sor_bytes
from sor_reader324802a import direction_key_from_genparams


st.set_page_config(page_title="Secret Sauce — Desktop", layout="wide")
st.title("Secret Sauce — Desktop")
st.caption(
    "Local edition — pick a folder of .sor / .trc / .json files on this "
    "computer. The report is written next to your files. Nothing leaves "
    "this machine, and there is no file-size limit."
)

# ----- Quit button (the packaged app has no console window to close) -----
with st.sidebar:
    st.markdown("### Secret Sauce")
    st.caption("When you're finished, click Quit to close the app.")
    if st.button("⏻ Quit Secret Sauce", use_container_width=True):
        st.success("Secret Sauce is shutting down — you can close this browser tab.")
        import os as _os
        _os._exit(0)   # hard-stop the local server process


# ----- native folder picker (works because we run locally) --------------
def _pick_folder():
    """Pop a native OS folder dialog. Returns the path, or '' on cancel/error."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        path = filedialog.askdirectory(title="Choose a folder of OTDR files")
        root.destroy()
        return path or ""
    except Exception:
        return ""   # tkinter unavailable → fall back to the text box below


if "folder" not in st.session_state:
    st.session_state["folder"] = ""

st.subheader("Step 1 — choose your folder of fiber files")
c1, c2 = st.columns([1, 2])
with c1:
    if st.button("📁  Browse for folder", type="primary",
                 use_container_width=True):
        picked = _pick_folder()
        if picked:
            st.session_state["folder"] = picked
with c2:
    st.session_state["folder"] = st.text_input(
        "…or paste a folder path here",
        value=st.session_state["folder"],
        placeholder=r"C:\Users\you\Desktop\fiber files",
    )

folder = st.session_state["folder"].strip().strip('"')
if not folder or not os.path.isdir(folder):
    st.info("👆 Click **Browse for folder** and pick the folder that holds your "
            ".sor / .trc / .json files. (A normal folder picker opens.)")
    st.stop()


# ----- inventory (recursive) --------------------------------------------
sor_files, trc_files, json_files = [], [], []
for root, _dirs, files in os.walk(folder):
    for f in files:
        low = f.lower()
        full = os.path.join(root, f)
        if low.endswith(".sor"):
            sor_files.append(full)
        elif low.endswith(".trc"):
            trc_files.append(full)
        elif low.endswith(".json"):
            json_files.append(full)

st.success(f"Found **{len(sor_files)} SOR** · **{len(trc_files)} TRC** · "
           f"**{len(json_files)} JSON** in this folder (and subfolders).")

n_kinds = sum(bool(x) for x in (sor_files, trc_files, json_files))
if n_kinds == 0:
    st.error("No .sor, .trc, or .json files found in that folder.")
    st.stop()
if n_kinds > 1:
    st.error("Mixed file types found. Keep one type (.sor / .trc / .json) "
             "per run.")
    st.stop()

output_format = st.radio("Output format", ["Excel (xlsx)", "PDF"],
                         horizontal=True,
                         help="Excel is the lightweight default. PDF is the "
                              "polished report with charts.")
want_xlsx = output_format.startswith("Excel")
ext = "xlsx" if want_xlsx else "pdf"


# ----- helpers ----------------------------------------------------------
def _stage_flat(paths):
    """Copy a list of files into a fresh flat temp dir (engine functions
    glob a single directory, non-recursively)."""
    td = tempfile.mkdtemp(prefix="ss_stage_")
    for p in paths:
        shutil.copy(p, os.path.join(td, os.path.basename(p)))
    return td


def _out_dir():
    d = os.path.join(folder, "SecretSauce_reports")
    os.makedirs(d, exist_ok=True)
    return d


# ----- run --------------------------------------------------------------
if st.button("Run analysis", type="primary"):
    out_dir = _out_dir()
    written = []

    try:
        if sor_files:
            # group by file-internal direction key (forward/reverse split)
            groups = defaultdict(list)
            for p in sor_files:
                key = direction_key_from_genparams(p) or os.path.basename(p)[:8]
                groups[key].append(p)
            groups = {k: v for k, v in groups.items() if len(v) >= 2}
            if not groups:
                st.error("Could not form a direction group with ≥2 SOR files.")
                st.stop()

            st.write(f"Detected **{len(groups)} direction group(s)**.")
            for key, paths in groups.items():
                stage = _stage_flat(paths)
                title = f"Secret Sauce — {key}"
                with st.spinner(f"Running {key} ({len(paths)} files)…"):
                    if want_xlsx:
                        data, nf, npairs = run_sor_xlsx_bytes(stage, title)
                    else:
                        data, nf, npairs = run_sor_bytes(stage, title)
                fname = (f"{key}_secret_sauce.{ext}" if len(groups) > 1
                         else f"report.{ext}")
                outp = os.path.join(out_dir, fname)
                with open(outp, "wb") as fh:
                    fh.write(data)
                written.append((outp, nf, npairs, key))
                shutil.rmtree(stage, ignore_errors=True)

        elif trc_files:
            stage = _stage_flat(trc_files)
            with st.spinner(f"Running {len(trc_files)} TRC files…"):
                if want_xlsx:
                    data, nf, npairs = run_trc_xlsx_bytes(stage, "Secret Sauce")
                else:
                    data, nf, npairs = run_trc_bytes(stage, "Secret Sauce")
            outp = os.path.join(out_dir, f"report.{ext}")
            with open(outp, "wb") as fh:
                fh.write(data)
            written.append((outp, nf, npairs, "TRC"))
            shutil.rmtree(stage, ignore_errors=True)

        else:  # json
            stage = _stage_flat(json_files)
            with st.spinner(f"Running {len(json_files)} JSON files…"):
                if want_xlsx:
                    data, nf, npairs = run_json_xlsx_bytes(stage, "Secret Sauce")
                else:
                    data, nf, npairs = run_json_bytes(stage, "Secret Sauce")
            outp = os.path.join(out_dir, f"report.{ext}")
            with open(outp, "wb") as fh:
                fh.write(data)
            written.append((outp, nf, npairs, "JSON"))
            shutil.rmtree(stage, ignore_errors=True)

    except Exception as exc:
        st.error(f"Analysis failed: {exc}")
        st.stop()

    st.success(f"Done — {len(written)} report(s) written to:")
    st.code(out_dir)
    for outp, nf, npairs, label in written:
        st.write(f"• **{label}** — {nf} files, {npairs} pairs → "
                 f"`{os.path.basename(outp)}`")
        with open(outp, "rb") as fh:
            st.download_button(
                f"Download {os.path.basename(outp)}",
                data=fh.read(),
                file_name=os.path.basename(outp),
                key=outp,
            )
