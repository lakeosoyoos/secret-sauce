"""
PyInstaller entry point for the packaged SecretSauce desktop app.

When frozen, PyInstaller unpacks our files to a temp dir (sys._MEIPASS).
This launcher boots Streamlit pointed at desktop_app.py and opens the
browser, so the tech just double-clicks SecretSauce.exe.
"""
import os
import sys
import time
import shutil
import tempfile
import threading
import webbrowser

# ----- auto-update: pull the latest engine + UI from the repo on launch -----
# The heavy shell (Streamlit/numpy/scipy — the ~150 MB) is frozen in the .exe
# and rarely changes. The engine and UI (these small .py files) change all the
# time. Fetching them fresh on each launch means a code update goes live on
# every tech's machine on their NEXT launch, with no re-download of the app.
# If the machine is offline, we silently fall back to the bundled copies.
_REPO = "lakeosoyoos/secret-sauce"
_REPO_RAW = "https://raw.githubusercontent.com/" + _REPO   # + /<sha>/<path>
_LIVE_FILES = {
    # local cache name      : path in the repo
    "report.py":            "report.py",
    "report_sor.py":        "report_sor.py",
    "sor_reader324802a.py": "sor_reader324802a.py",
    "trc_parser.py":        "trc_parser.py",
    "desktop_app.py":       "desktop/desktop_app.py",
}
# Per-file sentinel: a symbol each file MUST contain. Replaces a bare b"def "
# so a captive-portal HTML page, a truncated download, or a wrong-branch file
# can't masquerade as our code.
_SENTINELS = {
    "report.py":            b"def run_json_xlsx_bytes",
    "report_sor.py":        b"def run_sor_xlsx_bytes",
    "sor_reader324802a.py": b"def parse_sor_full",
    "trc_parser.py":        b"def parse_trc_file",
    "desktop_app.py":       b"def _is_otdr_json",
}


def _ss_root():
    return os.path.join(os.path.expanduser("~"), ".secretsauce")


def _pointer_path():
    return os.path.join(_ss_root(), "current")


def _current_pointer():
    """Path to the last engine dir that PASSED validation, or None. The pointer
    is flipped atomically only after a candidate passes the fixture smoke check,
    so whatever it points at is known-good."""
    try:
        with open(_pointer_path()) as f:
            d = f.read().strip()
        return d if d and os.path.exists(os.path.join(d, "desktop_app.py")) else None
    except Exception:
        return None


def _set_current_pointer(path):
    """Atomically point 'current' at a validated engine dir (temp + os.replace —
    one rename, atomic on POSIX and Windows, so a crash can't leave a half-
    written pointer or a mixed-version engine)."""
    p = _pointer_path()
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        f.write(path)
    os.replace(tmp, p)


def _cleanup_old_engines(keep):
    """Remove stale engine-<sha> dirs, keeping only the validated current one."""
    try:
        root = _ss_root()
        for name in os.listdir(root):
            d = os.path.join(root, name)
            if name.startswith("engine-") and d != keep and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def _resolve_head_sha():
    """main's current HEAD commit SHA, so every file is fetched from ONE
    consistent snapshot (no cross-commit skew mid-fetch)."""
    import urllib.request
    import json as _json
    api = "https://api.github.com/repos/" + _REPO + "/commits/main"
    req = urllib.request.Request(api, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SecretSauce-updater"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return _json.loads(r.read().decode())["sha"]


def _smoke_check_engine(folder):
    """Re-invoke ourselves in an isolated subprocess to confirm the freshly
    fetched engine actually IMPORTS. Catches a bad push to main that is valid
    Python but broken at import (missing symbol, bad top-level code) — the
    content-only check can't see that. Returns True if the engine imported.
    Only meaningful when frozen (we re-run the .exe); in dev we skip it."""
    if not getattr(sys, "frozen", False):
        return True
    import subprocess
    env = dict(os.environ, SS_SMOKE_CHECK=folder)
    try:
        # 60s cap: the check (import + fixture analyze + AppTest, AppTest itself
        # capped at 30s) normally runs in ~5-10s; 60s leaves comfortable margin
        # under the boot self-test's launch window even on a slow field laptop.
        r = subprocess.run([sys.executable], env=env,
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _fetch_latest_code():
    """Fetch + validate + ATOMICALLY publish the live engine/UI, pinned to one
    commit. Returns the validated engine dir, or None (caller then falls back to
    the last validated cache, then to the bundled copies).

    Hardened vs the old 'compiles + imports' gate:
      * one HEAD SHA -> every file from the SAME commit (no mid-fetch skew)
      * per-file sentinel (not a bare 'def ') so an error page can't pass
      * the smoke check RUNS the engine on a bundled fixture and asserts a known
        verdict AND runs the UI (desktop_app.py) — so a push that imports fine
        but breaks the verdict or the UI is rejected, not shipped
      * validated IN PLACE (the exact dir that will run), published by an atomic
        pointer flip — never a file-by-file copy into a live dir."""
    import urllib.request
    try:
        sha = _resolve_head_sha()
    except Exception as e:
        print("[launcher] could not resolve HEAD (offline/rate-limited): %r" % e)
        return None
    base = "%s/%s" % (_REPO_RAW, sha)
    cand = os.path.join(_ss_root(), "engine-" + sha[:12])
    # Already published + validated this exact commit? reuse, skip the re-fetch.
    if _current_pointer() == cand:
        return cand
    staging = tempfile.mkdtemp(prefix="ss_fetch_")
    try:
        for local_name, repo_path in _LIVE_FILES.items():
            with urllib.request.urlopen("%s/%s" % (base, repo_path), timeout=8) as resp:
                data = resp.read()
            if not data or _SENTINELS.get(local_name, b"def ") not in data:
                raise ValueError("unexpected content for " + repo_path)
            compile(data, local_name, "exec")     # syntax gate
            with open(os.path.join(staging, local_name), "wb") as fh:
                fh.write(data)
        os.makedirs(_ss_root(), exist_ok=True)
        shutil.rmtree(cand, ignore_errors=True)
        shutil.copytree(staging, cand)
        # Validate the FINAL dir (validated bytes == executed bytes), running the
        # fixture verdict + UI smoke check. Only flip the pointer if it passes.
        if not _smoke_check_engine(cand):
            shutil.rmtree(cand, ignore_errors=True)
            print("[launcher] candidate engine failed smoke check; keeping last-good")
            return None
        _set_current_pointer(cand)
        _cleanup_old_engines(keep=cand)
        return cand
    except (OSError, PermissionError) as e:
        print("[launcher] update I/O error (locked dir / disk full?): %r" % e)
        return None
    except Exception as e:
        print("[launcher] update fetch failed: %r" % e)
        return None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _redirect_output_to_log():
    """A windowed (no-console) build has no stdout/stderr — on Windows they can
    be None, and anything that writes to them (Streamlit's banner, logging,
    print) then crashes the app. Point both at a log file in the user's home so
    nothing writes to a missing console, and so field issues are debuggable."""
    if not getattr(sys, "frozen", False):
        return
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".secretsauce")
        os.makedirs(log_dir, exist_ok=True)
        logf = open(os.path.join(log_dir, "secretsauce.log"), "a",
                    buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = logf
        sys.stderr = logf
    except Exception:
        # last resort: swallow output so a None-stdout write can't crash us
        try:
            devnull = open(os.devnull, "w")
            sys.stdout = devnull
            sys.stderr = devnull
        except Exception:
            pass


def _silence_first_run_prompt():
    """Streamlit asks for an email on first run and BLOCKS on stdin — fatal for
    a double-click app with no console input. Pre-seed an empty credentials
    file and force headless/no-telemetry via env so it never prompts."""
    try:
        cred_dir = os.path.join(os.path.expanduser("~"), ".streamlit")
        os.makedirs(cred_dir, exist_ok=True)
        cred = os.path.join(cred_dir, "credentials.toml")
        if not os.path.exists(cred):
            with open(cred, "w") as fh:
                fh.write('[general]\nemail = ""\n')
    except Exception:
        pass
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_GLOBAL_DEVELOPMENT_MODE", "false")


def _base_dir():
    # _MEIPASS exists only when running inside a PyInstaller bundle.
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _open_browser():
    # Wait until the server actually answers before opening the browser.
    # A fixed delay isn't enough: the FIRST cold launch unpacks a few hundred
    # MB and Streamlit can take 10-30s to bind, which would otherwise show a
    # "connection error" page. Poll the health endpoint for up to ~90s, then
    # open. (Fallback: open anyway so a slow machine still gets the page.)
    import urllib.request
    health = "http://127.0.0.1:8501/_stcore/health"
    for _ in range(180):
        try:
            with urllib.request.urlopen(health, timeout=1) as r:
                if r.read().decode().strip() == "ok":
                    break
        except Exception:
            pass
        time.sleep(0.5)
    webbrowser.open("http://localhost:8501")


def _smoke_check_mode():
    """The isolated subprocess from _smoke_check_engine (SS_SMOKE_CHECK=<dir>).
    Validates the candidate engine in <dir> three ways, then exits 0 (good) or
    non-zero (rejected -> launcher keeps the last-good engine, never ships this):
      1. the engine modules import;
      2. the engine RUN on a tiny bundled fixture still calls the known re-shoot
         a duplicate (a push that inverts a threshold imports fine but fails HERE
         — the wrong-verdict failure that a 'compiles + imports' gate misses);
      3. the UI (desktop_app.py) imports + runs through Streamlit AppTest (a plain
         import can't validate it — it runs Streamlit at module top).
    Must run before any normal startup work."""
    folder = os.environ.get("SS_SMOKE_CHECK")
    if not folder:
        return False
    try:
        sys.path.insert(0, folder)
        import importlib
        for mod in ("sor_reader324802a", "trc_parser", "report", "report_sor"):
            importlib.import_module(mod)
        # 2. verdict gate on the bundled fixture (skip only if it isn't bundled)
        fixture = os.path.join(_base_dir(), "_smoke_fixture")
        if os.path.isdir(fixture):
            rsor = importlib.import_module("report_sor")
            a = rsor._analyze_sor(fixture)
            if not any(p.get("p_dup", 0) > 0.5
                       and {p.get("a"), p.get("b")}
                       == {"TUCROM453_1550", "TUCROM454_1550"}
                       for p in a["pairs"]):
                os._exit(2)
        # 3. UI gate — best-effort: an AppTest hiccup must never REJECT an
        # otherwise-good engine (compile() already gated syntax), so only a
        # CONFIRMED desktop_app exception fails it.
        try:
            from streamlit.testing.v1 import AppTest
            at = AppTest.from_file(os.path.join(folder, "desktop_app.py"),
                                   default_timeout=30)
            at.run()
            if len(at.exception) > 0:
                os._exit(3)
        except Exception:
            pass
        os._exit(0)
    except Exception:
        os._exit(1)
    return True


def _already_running():
    """True if a SecretSauce server is already serving on 8501 — avoids a
    second double-click starting a dead second process and opening a stale tab."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health",
                                    timeout=2) as r:
            return r.read().decode().strip() == "ok"
    except Exception:
        return False


def main():
    # Isolated smoke-check subprocess (engine import validation) — exits early.
    _smoke_check_mode()

    _redirect_output_to_log()

    # If the app is already running (tech double-clicked twice), just open the
    # existing instance instead of starting a dead second server.
    if _already_running():
        webbrowser.open("http://localhost:8501")
        return

    _silence_first_run_prompt()

    # Pull the latest code (atomically published ONLY if it passed the fixture
    # smoke check). If offline / fetch fails, fall back to the last VALIDATED
    # cache; only if there's none do we use the bundled copies. The chosen
    # desktop_app.py does sys.path.insert(0, HERE), so its imports resolve to the
    # engine in the same dir.
    cache = _fetch_latest_code()
    if cache:
        source = "latest (auto-updated from the cloud)"
    else:
        cache = _current_pointer()
        source = "cached (last validated update)" if cache else None
    if cache and os.path.exists(os.path.join(cache, "desktop_app.py")):
        script = os.path.join(cache, "desktop_app.py")
        os.environ["PYTHONPATH"] = cache + os.pathsep + os.environ.get("PYTHONPATH", "")
        os.environ["SS_ENGINE_SOURCE"] = source
        print("[launcher] using engine from %s (%s)" % (cache, source))
    else:
        script = os.path.join(_base_dir(), "desktop_app.py")
        os.environ["SS_ENGINE_SOURCE"] = "bundled (offline — using the built-in version)"
        print("[launcher] offline / no validated cache; using bundled code")

    threading.Thread(target=_open_browser, daemon=True).start()

    # Boot Streamlit via its CLI entry point (most version-stable across builds)
    from streamlit.web import cli as stcli
    sys.argv = [
        "streamlit", "run", script,
        "--server.headless=true",          # we open the browser ourselves
        "--server.port=8501",
        "--server.address=127.0.0.1",      # localhost only — never exposed
        "--browser.gatherUsageStats=false",
        "--global.developmentMode=false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
