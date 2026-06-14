"""Configuration / control tests for the Secret Sauce desktop app (Agent B).

These cover the *controls* a tech actually touches and the wiring between them
and the engine — as opposed to the end-to-end "does a report come out" path that
test_e2e_run.py already owns. Specifically:

  * the Output-format radio (Excel vs PDF) really changes the produced file type;
  * the folder path the user types actually flows into the engine (the report
    lands where the path points);
  * the NaN/empty/whitespace/bad-path guards stop the app *before* any work and
    never write a report — even when the run is force-triggered;
  * the Streamlit widget-key-vs-side-slot precedence: the folder text_input is
    keyless and bound via `value=st.session_state["folder"]`, so MUTATING that
    bound slot is what drives the value (a side slot the widget does NOT read is
    ignored);
  * a STATIC DRIFT check that desktop_app.py and app.py agree on the set of
    output formats they offer, so the two twins can't silently diverge.

Driving conventions (see test_e2e_run.py for the rationale):
  * st.session_state["folder"] is the bound state slot the path text_input reads
    through value=, so seeding it is how a folder is injected.
  * st.session_state["__test_force_run__"] = True fires the run branch headlessly
    via the production no-op test hook. It is never set by any UI element.

The Output-format radio carries NO explicit key=, so it cannot be pre-seeded by
key before its first render. The reliable drive is: render once (no force), call
radio.set_value(...), THEN set the force flag and run — so the run branch reads
the chosen format. If the force flag were set on the very first run, the run
would fire with the radio at its default (Excel) before set_value could land,
writing the wrong file type. That ordering itself is the keyless-widget footgun
pinned by test_radio_has_no_stable_key_TODO / test_radio_should_have_stable_key.
"""
import ast
import os
import shutil

import pytest

from conftest import run_streamlit, FIXTURE_SOR_DIR

# Repo layout: desktop/tests/ -> desktop/ -> repo root (where app.py lives).
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DESKTOP_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(DESKTOP_DIR)
DESKTOP_APP = os.path.join(DESKTOP_DIR, "desktop_app.py")
CLOUD_APP = os.path.join(REPO_ROOT, "app.py")

# Magic bytes that unambiguously identify each output container.
PDF_MAGIC = b"%PDF"        # PDF header
XLSX_MAGIC = b"PK\x03\x04"  # XLSX is a ZIP


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _copy_fixture(src_dir, tmp_path, name):
    """Copy a committed fixture folder into throwaway temp space."""
    dest = os.path.join(str(tmp_path), name)
    shutil.copytree(src_dir, dest)
    return dest


def _report_dir(folder):
    return os.path.join(folder, "SecretSauce_reports")


def _list_reports(folder):
    out = _report_dir(folder)
    if not os.path.isdir(out):
        return []
    return sorted(os.listdir(out))


def _drive_excel(folder):
    """Force-run with the default (Excel) output format. Returns the AppTest."""
    at = run_streamlit(default_timeout=120)
    at.session_state["folder"] = folder
    at.session_state["__test_force_run__"] = True
    at.run()
    return at


def _drive_pdf(folder):
    """Force-run with the PDF output format selected via the radio.

    Render once WITHOUT the force flag so the radio exists, flip it to PDF, then
    set the force flag and run — so only the PDF report is written (no stray
    Excel from an early default-fire). Returns the AppTest.
    """
    at = run_streamlit(default_timeout=120)
    at.session_state["folder"] = folder
    at.run()  # render the radio; run branch NOT fired yet
    _set_output_radio(at, "PDF")
    at.session_state["__test_force_run__"] = True
    at.run()
    return at


def _set_output_radio(at, label_startswith):
    """Set the Output-format radio to the option starting with `label_startswith`.

    The app keys off output_format.startswith("Excel"); matching on the option
    prefix keeps this robust to the parenthetical in "Excel (xlsx)".
    """
    radios = [r for r in at.radio if r.label == "Output format"]
    assert radios, "Output format radio did not render"
    radio = radios[0]
    target = next(o for o in radio.options if o.startswith(label_startswith))
    radio.set_value(target)
    return radio


def _radio_options(path):
    """AST-extract the options list of every st.radio(...) call in a source file.

    Reads options from the `options=` kwarg or the 2nd positional arg, matching
    how Streamlit accepts them. Returns a list of option-lists.
    """
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != "radio":
            continue
        opts = None
        for kw in node.keywords:
            if kw.arg == "options":
                opts = ast.literal_eval(kw.value)
        if opts is None and len(node.args) >= 2:
            try:
                opts = ast.literal_eval(node.args[1])
            except (ValueError, SyntaxError):
                opts = None
        if opts is not None:
            found.append(list(opts))
    return found


def _widget_key_kwargs(path, widget_name):
    """Return the literal value of every `key=` kwarg on a given widget call.

    A keyless widget contributes None; a `key=` whose value isn't a literal
    contributes the sentinel "<expr>".
    """
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    keys = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != widget_name:
            continue
        k = None
        for kw in node.keywords:
            if kw.arg == "key":
                try:
                    k = ast.literal_eval(kw.value)
                except (ValueError, SyntaxError):
                    k = "<expr>"
        keys.append(k)
    return keys


# ---------------------------------------------------------------------------
# Output-format radio actually changes the produced file type
# ---------------------------------------------------------------------------
def test_output_format_default_excel_produces_xlsx(tmp_path):
    """Default (no radio touch) writes an .xlsx whose bytes are a real ZIP."""
    folder = _copy_fixture(FIXTURE_SOR_DIR, tmp_path, "sor")
    at = _drive_excel(folder)

    assert len(at.exception) == 0, repr([e.value for e in at.exception])
    reports = _list_reports(folder)
    assert reports == ["report.xlsx"], (
        f"Excel default should write exactly report.xlsx, got {reports}"
    )
    with open(os.path.join(_report_dir(folder), "report.xlsx"), "rb") as fh:
        head = fh.read(8)
    assert head.startswith(XLSX_MAGIC), (
        f"report.xlsx is not a real XLSX/ZIP container (head={head!r})"
    )
    assert not head.startswith(PDF_MAGIC), "Excel run wrote PDF bytes"


def test_output_format_pdf_radio_produces_pdf(tmp_path):
    """Selecting the PDF radio writes a .pdf whose bytes start with %PDF."""
    folder = _copy_fixture(FIXTURE_SOR_DIR, tmp_path, "sor")
    at = _drive_pdf(folder)

    assert len(at.exception) == 0, repr([e.value for e in at.exception])
    assert at.radio[0].value.startswith("PDF"), (
        f"radio did not take the PDF value (got {at.radio[0].value!r})"
    )
    reports = _list_reports(folder)
    assert reports == ["report.pdf"], (
        "PDF radio should write exactly report.pdf (no stray Excel from an "
        f"early default-fire), got {reports}"
    )
    with open(os.path.join(_report_dir(folder), "report.pdf"), "rb") as fh:
        head = fh.read(8)
    assert head.startswith(PDF_MAGIC), (
        f"report.pdf is not a real PDF (head={head!r})"
    )
    assert not head.startswith(XLSX_MAGIC), "PDF run wrote XLSX/ZIP bytes"


def test_radio_choice_changes_extension(tmp_path):
    """Same input + same engine: only the radio differs, and the file
    EXTENSION flips with it. This is the control->output contract in one
    assertion (Excel => .xlsx, PDF => .pdf, never both)."""
    folder_x = _copy_fixture(FIXTURE_SOR_DIR, tmp_path, "sor_x")
    folder_p = _copy_fixture(FIXTURE_SOR_DIR, tmp_path, "sor_p")

    _drive_excel(folder_x)
    _drive_pdf(folder_p)

    excel_reports = _list_reports(folder_x)
    pdf_reports = _list_reports(folder_p)
    assert excel_reports == ["report.xlsx"], excel_reports
    assert pdf_reports == ["report.pdf"], pdf_reports
    # The two runs produced different container extensions purely from the radio.
    assert {r.rsplit(".", 1)[-1] for r in excel_reports} == {"xlsx"}
    assert {r.rsplit(".", 1)[-1] for r in pdf_reports} == {"pdf"}


# ---------------------------------------------------------------------------
# Folder path flows to the engine (report lands where the path points)
# ---------------------------------------------------------------------------
def test_folder_path_flows_to_engine(tmp_path):
    """The report is written under SecretSauce_reports/ INSIDE the chosen folder,
    proving the typed path reached _out_dir() and the engine — not some default
    or cwd location."""
    folder = _copy_fixture(FIXTURE_SOR_DIR, tmp_path, "sor")
    at = _drive_excel(folder)

    assert len(at.exception) == 0, repr([e.value for e in at.exception])
    out_dir = _report_dir(folder)
    assert os.path.isdir(out_dir), (
        f"report dir not created inside the chosen folder: {out_dir}"
    )
    report = os.path.join(out_dir, "report.xlsx")
    assert os.path.isfile(report), f"engine output missing at {report}"
    # The success banner echoes the exact out_dir, confirming the path round-trip.
    codes = [c.value for c in at.code]
    assert any(out_dir in (c or "") for c in codes), (
        f"success banner did not echo the chosen out_dir {out_dir}; codes={codes}"
    )


# ---------------------------------------------------------------------------
# NaN / empty / whitespace / bad-path guards
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_value",
    [
        "",                       # empty
        "   ",                    # whitespace-only (empty after .strip())
        '""',                     # bare quotes (empty after .strip('"'))
        "/no/such/path/ss_xyz",  # non-existent directory
    ],
    ids=["empty", "whitespace", "bare_quotes", "nonexistent"],
)
def test_bad_path_guard_stops_before_run(bad_value):
    """An empty/whitespace/quote-only/nonexistent path must hit the info guard,
    render NO output-format radio, and surface no exception — the app stops at
    Step 1 before doing any work."""
    at = run_streamlit(default_timeout=60)
    at.session_state["folder"] = bad_value
    at.run()

    assert len(at.exception) == 0, repr([e.value for e in at.exception])
    infos = [i.value for i in at.info]
    assert any("Browse for folder" in (m or "") for m in infos), (
        f"expected the Step-1 info guard for {bad_value!r}; infos={infos}"
    )
    # Guard short-circuits before the radio renders.
    assert not any(r.label == "Output format" for r in at.radio), (
        f"output radio rendered despite bad path {bad_value!r}"
    )


def test_bad_path_writes_no_report_even_when_forced(tmp_path):
    """The force-run hook must NOT be able to bypass the path guard: with a
    nonexistent folder and __test_force_run__ set, there is no success banner
    and (critically) no report is written anywhere under tmp_path."""
    bad = os.path.join(str(tmp_path), "does_not_exist")
    at = run_streamlit(default_timeout=60)
    at.session_state["folder"] = bad
    at.session_state["__test_force_run__"] = True
    at.run()

    assert len(at.exception) == 0, repr([e.value for e in at.exception])
    successes = [s.value for s in at.success]
    assert not any("Done" in (m or "") for m in successes), (
        f"run completed on a bad path (should have stopped): {successes}"
    )
    # Nothing was written: no SecretSauce_reports dir should exist for a path
    # the guard rejected.
    assert not os.path.exists(os.path.join(bad, "SecretSauce_reports")), (
        "a report dir was created for a guarded-out path"
    )


# ---------------------------------------------------------------------------
# Widget-key vs side-slot precedence
# ---------------------------------------------------------------------------
def test_folder_bound_slot_drives_text_input(tmp_path):
    """The folder text_input is keyless and reads value=st.session_state["folder"],
    so mutating that BOUND slot drives the widget value on the next run. This pins
    the keyless+value drive pattern the whole suite relies on."""
    folder = _copy_fixture(FIXTURE_SOR_DIR, tmp_path, "sor")
    at = run_streamlit(default_timeout=60)
    at.session_state["folder"] = folder
    at.run()

    # The widget shows the seeded value...
    assert at.text_input[0].value == folder, (
        f"bound slot did not drive the text_input (got {at.text_input[0].value!r})"
    )
    # ...and it propagated far enough that the app advanced past Step 1 (the
    # output radio only renders once a valid folder is accepted).
    assert any(r.label == "Output format" for r in at.radio), (
        "seeding the bound 'folder' slot did not advance past the path guard"
    )


def test_unbound_side_slot_is_ignored(tmp_path):
    """Precedence proof: a side slot the widget does NOT read has no effect.
    Writing a bogus key while leaving the bound 'folder' slot empty must leave
    the app stuck at the Step-1 guard — only the bound slot drives the value."""
    folder = _copy_fixture(FIXTURE_SOR_DIR, tmp_path, "sor")
    at = run_streamlit(default_timeout=60)
    # Put the real path in a slot the text_input never reads.
    at.session_state["not_the_folder_slot"] = folder
    # Leave the bound slot empty.
    at.session_state["folder"] = ""
    at.run()

    assert at.text_input[0].value == "", (
        "an unbound side slot leaked into the text_input value"
    )
    infos = [i.value for i in at.info]
    assert any("Browse for folder" in (m or "") for m in infos), (
        "app advanced past the guard from an unbound side slot — precedence "
        f"violated; infos={infos}"
    )
    assert not any(r.label == "Output format" for r in at.radio)


# ---------------------------------------------------------------------------
# STATIC DRIFT: desktop_app.py vs app.py controls
# ---------------------------------------------------------------------------
def test_both_apps_exist_for_drift_check():
    """Guard the drift test itself: both twins must be present to compare."""
    assert os.path.isfile(DESKTOP_APP), f"missing {DESKTOP_APP}"
    assert os.path.isfile(CLOUD_APP), f"missing {CLOUD_APP}"


def test_output_format_options_agree_across_apps():
    """desktop_app.py and app.py must offer the SAME set of output formats.

    Both twins compute want_xlsx via output_format.startswith("Excel"), so if one
    file's radio options drift (a renamed/added/removed format) the two apps
    would silently disagree on what a tech can pick — and the .startswith gate
    could misfire. Compared as sets so option ORDER (which only sets the radio
    default) is allowed to differ, but the OPTIONS themselves must match."""
    desk = _radio_options(DESKTOP_APP)
    cloud = _radio_options(CLOUD_APP)
    assert len(desk) == 1, f"expected exactly one radio in desktop_app.py, got {desk}"
    assert len(cloud) == 1, f"expected exactly one radio in app.py, got {cloud}"
    assert set(desk[0]) == set(cloud[0]), (
        "output-format options drifted between the twins: "
        f"desktop={desk[0]} vs cloud={cloud[0]}"
    )
    # And both must still carry the two formats the engine + .startswith gate
    # actually understand, so neither file can drop one unnoticed.
    assert set(desk[0]) == {"Excel (xlsx)", "PDF"}, (
        f"desktop output formats changed unexpectedly: {desk[0]}"
    )


def test_excel_startswith_gate_matches_an_option():
    """Both apps decide format via output_format.startswith("Excel"). Guard that
    the gate prefix still matches exactly one option in each app (and never the
    PDF one) — so a future rename of the Excel option can't silently make the
    gate fall through to PDF for everyone."""
    for label, path in (("desktop", DESKTOP_APP), ("cloud", CLOUD_APP)):
        opts = _radio_options(path)[0]
        excel_matches = [o for o in opts if o.startswith("Excel")]
        assert len(excel_matches) == 1, (
            f"{label}: the .startswith('Excel') gate must match exactly one "
            f"option, matched {excel_matches} of {opts}"
        )
        assert not any(o.startswith("Excel") and "PDF" in o for o in opts)


# ---------------------------------------------------------------------------
# Load-bearing footgun: keyless output radio.
#
# The Output-format radio in desktop_app.py carries NO explicit key=. Because of
# that, a test (or any programmatic driver / concurrent rerun) cannot pre-seed
# the choice by a stable widget key BEFORE the radio first renders — it must
# render the widget, then set_value, then rerun. If the run is triggered on the
# same pass the radio first appears (as the force hook does on a first run), the
# radio is still at its default and the WRONG output format is produced. That is
# exactly the widget-state trap called out in the project memory ("never write a
# value to a widget you also drive programmatically without a stable key").
#
# We pin BOTH states so a fix forces a paired update:
#   * PASS + TODO: documents that the radio is keyless TODAY.
#   * xfail(strict=True): the DESIRED state (a stable key=) — the day someone
#     adds key="output_format", this XPASSes and fails the build, prompting the
#     keyless-pin TODO above to be removed in the same change.
# ---------------------------------------------------------------------------
def test_output_radio_is_keyless_TODO():
    """PINS CURRENT BEHAVIOUR (footgun). The desktop Output-format radio has no
    key=, so it can only be driven via render->set_value->rerun, never pre-seeded
    by key. TODO: give it key="output_format" so tests/concurrent reruns can set
    it deterministically; when that lands, delete this test and the companion
    xfail below will XPASS and force the update."""
    keys = _widget_key_kwargs(DESKTOP_APP, "radio")
    assert keys == [None], (
        f"expected the desktop radio to still be keyless; got key(s)={keys}. "
        "If a key was added, remove this TODO test and update its xfail twin."
    )


@pytest.mark.xfail(
    strict=True,
    reason="DESIRED: the Output-format radio should carry a stable key= so it "
           "can be pre-seeded before first render (avoids the keyless widget-"
           "state trap). It is keyless today; remove the TODO twin when fixed.",
)
def test_output_radio_should_have_stable_key():
    """DESIRED BEHAVIOUR (xfail until fixed). The Output-format radio should
    declare a literal key= so a value can be driven by key, not just by
    post-render set_value."""
    keys = _widget_key_kwargs(DESKTOP_APP, "radio")
    assert keys and all(isinstance(k, str) and k for k in keys), (
        f"radio still lacks a stable string key=; got {keys}"
    )


def test_folder_text_input_is_keyless_TODO():
    """PINS CURRENT BEHAVIOUR (footgun). The folder path text_input has no key=;
    it is driven solely through value=st.session_state["folder"] (the bound side
    slot). TODO: a stable key="folder" would let the widget own its state instead
    of the manual slot round-trip. When added, delete this and its xfail twin
    XPASSes."""
    keys = _widget_key_kwargs(DESKTOP_APP, "text_input")
    assert keys == [None], (
        f"expected the folder text_input to still be keyless; got {keys}."
    )


@pytest.mark.xfail(
    strict=True,
    reason="DESIRED: the folder path text_input should carry a stable key= so "
           "Streamlit owns its state instead of the manual value=+side-slot "
           "round-trip. Keyless today; remove the TODO twin when fixed.",
)
def test_folder_text_input_should_have_stable_key():
    """DESIRED BEHAVIOUR (xfail until fixed)."""
    keys = _widget_key_kwargs(DESKTOP_APP, "text_input")
    assert keys and all(isinstance(k, str) and k for k in keys), (
        f"folder text_input still lacks a stable string key=; got {keys}"
    )
