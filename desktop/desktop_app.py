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
import zipfile
from collections import defaultdict

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from report import (run_json_xlsx_bytes, run_trc_xlsx_bytes,
                    run_json_bytes, run_trc_bytes)
from report_sor import (run_sor_xlsx_bytes, run_sor_bytes,
                        group_sor_by_direction, bidirectional_sanity_check)


st.set_page_config(page_title="Secret Sauce — Desktop", layout="wide")
st.title("Secret Sauce — Desktop")
st.caption(
    "Local edition — pick a folder of .sor / .trc / .json files on this "
    "computer. The report is written next to your files. Nothing leaves "
    "this machine, and there is no file-size limit."
)

# ----- Quit button + version indicator -----------------------------------
with st.sidebar:
    st.markdown("### Secret Sauce")
    # Show whether we're on the auto-updated (online) or bundled (offline) code,
    # so it's clear the app pulls the latest version on launch.
    _src = os.environ.get("SS_ENGINE_SOURCE", "")
    if _src:
        if _src.startswith("latest"):
            st.caption("✅ Version: " + _src)
        else:
            st.caption("⚠️ Version: " + _src)
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


def _is_otdr_json(path):
    """True only if this .json is actually an OTDR trace. The engine's
    report.load_file() requires d['Measurement']['OtdrMeasurements'], so a real
    trace file always contains that literal key; a stray results/config json
    (e.g. a leftover rmse_results.json sitting next to the traces) does not.
    Byte-substring sniff — no full JSON parse, so it stays cheap even on a
    1000+ file folder, and it can never drop a real trace (the engine couldn't
    read one without that key anyway)."""
    try:
        with open(path, "rb") as fh:
            return b"OtdrMeasurements" in fh.read()
    except Exception:
        return False


def _extract_zips_in(folder):
    """Find .zip files anywhere under `folder`, extract their .sor/.trc/.json into
    a temp dir (one subfolder per zip so same-named files can't collide), and
    return (extract_dir or None, n_zips). Cached in st.session_state so a big zip
    isn't re-extracted on every Streamlit rerun — it re-extracts only when the set
    of zips (path/size/mtime) changes. Lets a tech point at a folder of zipped
    acquisitions (or drop the zips straight in) and just run."""
    zips = []
    for root, _d, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".zip"):
                zips.append(os.path.join(root, f))
    if not zips:
        old = st.session_state.pop("_zip_dir", None)
        if old:
            shutil.rmtree(old, ignore_errors=True)
        st.session_state.pop("_zip_sig", None)
        return None, 0
    sig = tuple(sorted((z, os.path.getsize(z), int(os.path.getmtime(z)))
                       for z in zips))
    if st.session_state.get("_zip_sig") == sig and st.session_state.get("_zip_dir"):
        return st.session_state["_zip_dir"], len(zips)
    old = st.session_state.get("_zip_dir")
    if old:
        shutil.rmtree(old, ignore_errors=True)
    dest = tempfile.mkdtemp(prefix="ss_zip_")
    for z in zips:
        sub = os.path.join(dest, os.path.splitext(os.path.basename(z))[0])
        os.makedirs(sub, exist_ok=True)
        try:
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    inner = os.path.basename(info.filename)
                    low = inner.lower()
                    if (not inner or inner.startswith("._") or inner == ".DS_Store"
                            or not low.endswith((".sor", ".trc", ".json"))):
                        continue
                    with zf.open(info) as src, \
                            open(os.path.join(sub, inner), "wb") as out:
                        out.write(src.read())
        except Exception as exc:
            st.warning(f"⚠️ Could not read {os.path.basename(z)}: {exc}")
    st.session_state["_zip_sig"] = sig
    st.session_state["_zip_dir"] = dest
    return dest, len(zips)


# ----- inventory (recursive, incl. any .zip archives in the folder) ------
# Any .zip in the chosen folder is extracted and its .sor/.trc/.json included.
# Non-trace .json files (results/config exports) are ignored so they can't trip
# the one-type-per-run guard below. SOR/TRC are bucketed by extension (stray
# files with those OTDR-specific extensions don't occur in the field).
zip_dir, n_zips = _extract_zips_in(folder)
roots = [folder] + ([zip_dir] if zip_dir else [])
sor_files, trc_files, json_files = [], [], []
skipped_json = 0
for base in roots:
    for root, _dirs, files in os.walk(base):
        for f in files:
            if f.startswith("._"):
                continue   # macOS AppleDouble sidecar, not a trace (see 05eabe2)
            low = f.lower()
            full = os.path.join(root, f)
            if low.endswith(".sor"):
                sor_files.append(full)
            elif low.endswith(".trc"):
                trc_files.append(full)
            elif low.endswith(".json"):
                if _is_otdr_json(full):
                    json_files.append(full)
                else:
                    skipped_json += 1   # stray results/config json — not a trace

st.success(f"Found **{len(sor_files)} SOR** · **{len(trc_files)} TRC** · "
           f"**{len(json_files)} JSON** in this folder (and subfolders)"
           + (f", including **{n_zips} zip archive(s)**." if n_zips else "."))
if skipped_json:
    st.caption(f"ℹ️ Ignored {skipped_json} non-trace .json file"
               f"{'s' if skipped_json != 1 else ''} (e.g. a results or config "
               f"file) so it won't block the run.")

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
    """Copy a list of files into a fresh flat temp dir (engine functions glob a
    single directory, non-recursively). De-duplicates basenames so two files
    with the same name in different subfolders don't silently overwrite each
    other (OTDR exports reuse names per job folder). SOR grouping keys off the
    file's internal GenParams, not the filename, so renaming is safe."""
    td = tempfile.mkdtemp(prefix="ss_stage_")
    used = set()
    for p in paths:
        base = os.path.basename(p)
        dest = base
        i = 1
        while dest.lower() in used:
            stem, ext = os.path.splitext(base)
            dest = f"{stem}__{i}{ext}"
            i += 1
        used.add(dest.lower())
        shutil.copy(p, os.path.join(td, dest))
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
            # Group by shot direction, ROBUST to mislabeled GenParams. If one
            # GenParams key spans multiple filename families (i.e. two shot
            # directions carrying the same A->B codes), split by filename prefix
            # and warn instead of silently merging directions into one analysis.
            groups, dir_warnings = group_sor_by_direction(sor_files)
            groups = {k: v for k, v in groups.items() if len(v) >= 2}
            for w in dir_warnings:
                st.warning("⚠️ " + w)
            if not groups:
                st.error("Could not form a direction group with ≥2 SOR files.")
                st.stop()

            st.write(f"Detected **{len(groups)} direction group(s)**.")
            dir_summaries = []
            for key, paths in groups.items():
                stage = _stage_flat(paths)
                title = f"Secret Sauce — {key}"
                with st.spinner(f"Running {key} ({len(paths)} files)…"):
                    if want_xlsx:
                        data, nf, npairs, ndup = run_sor_xlsx_bytes(stage, title)
                    else:
                        data, nf, npairs, ndup = run_sor_bytes(stage, title)
                fname = (f"{key}_secret_sauce.{ext}" if len(groups) > 1
                         else f"report.{ext}")
                outp = os.path.join(out_dir, fname)
                with open(outp, "wb") as fh:
                    fh.write(data)
                written.append((outp, nf, npairs, key))
                dir_summaries.append({'key': key, 'n50': ndup, 'n_pairs': npairs})
                shutil.rmtree(stage, ignore_errors=True)

            # Bidirectional sanity signal: real duplicates appear in BOTH shot
            # directions, so a forward-says-many / reverse-says-none split points
            # to a shared-structure artifact, not real duplication.
            susp, msg = bidirectional_sanity_check(dir_summaries)
            if susp:
                st.warning("⚠️ " + msg)

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
