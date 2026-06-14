"""Auto-update hardening tests (launcher.py).

Locks the invariants the fixture-gated atomic auto-update relies on, so a future
change that breaks them fails CI instead of silently shipping a wrong-verdict
engine or bricking the atomic publish. See the tech-flow audit (the biggest-
improvement fix): the launcher now resolves one HEAD SHA, validates per-file
sentinels, RUNS the candidate engine on a bundled fixture asserting a known
verdict (and runs the UI), and publishes by an atomic pointer flip with a
last-good fallback.
"""
import os
import sys
import unittest.mock as mock

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
sys.path.insert(0, DESKTOP)

import launcher as L          # noqa: E402  (main() is __main__-guarded; safe to import)
import report_sor as RS       # noqa: E402

SMOKE_FIXTURE = os.path.join(DESKTOP, "tests", "fixtures", "sor")


def test_smoke_fixture_still_detects_the_known_dup():
    """The launcher's auto-update smoke check RUNS the candidate engine on the
    bundled SOR fixture and asserts the TUCROM453~454 re-shoot is a duplicate.
    If this invariant ever breaks, the launcher would reject ALL updates — so
    this test guards the gate itself, not just the engine."""
    a = RS._analyze_sor(SMOKE_FIXTURE)
    assert any(
        p.get("p_dup", 0) > 0.5
        and {p.get("a"), p.get("b")} == {"TUCROM453_1550", "TUCROM454_1550"}
        for p in a["pairs"]
    ), "smoke-fixture dup not detected — the launcher verdict gate would reject every update"


def test_every_live_file_has_a_sentinel():
    assert set(L._LIVE_FILES) == set(L._SENTINELS)


def test_sentinels_present_in_the_bundled_files():
    for name, sent in L._SENTINELS.items():
        path = os.path.join(DESKTOP, name)
        assert sent in open(path, "rb").read(), f"{name} missing sentinel {sent!r}"


def test_pointer_is_atomic_and_round_trips(tmp_path):
    with mock.patch.object(L, "_ss_root", lambda: str(tmp_path)):
        eng = tmp_path / "engine-abc123def456"
        eng.mkdir()
        (eng / "desktop_app.py").write_text("")
        L._set_current_pointer(str(eng))
        assert L._current_pointer() == str(eng)
        assert not (tmp_path / "current.tmp").exists()   # no leftover temp


def test_pointer_rejects_a_dir_missing_desktop_app(tmp_path):
    with mock.patch.object(L, "_ss_root", lambda: str(tmp_path)):
        eng = tmp_path / "engine-empty"
        eng.mkdir()
        L._set_current_pointer(str(eng))
        assert L._current_pointer() is None   # incomplete dir is not "current"


def test_cleanup_keeps_current_removes_stale(tmp_path):
    with mock.patch.object(L, "_ss_root", lambda: str(tmp_path)):
        keep = tmp_path / "engine-keep"; keep.mkdir()
        stale = tmp_path / "engine-old"; stale.mkdir()
        L._cleanup_old_engines(keep=str(keep))
        assert keep.exists() and not stale.exists()


def test_spec_bundles_the_smoke_fixture():
    spec = open(os.path.join(DESKTOP, "SecretSauce.spec")).read()
    assert "_smoke_fixture" in spec and "tests/fixtures/sor" in spec
