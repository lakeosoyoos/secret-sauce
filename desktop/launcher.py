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
_REPO_RAW = "https://raw.githubusercontent.com/lakeosoyoos/secret-sauce/main"
_LIVE_FILES = {
    # local cache name      : path in the repo
    "report.py":            "report.py",
    "report_sor.py":        "report_sor.py",
    "sor_reader324802a.py": "sor_reader324802a.py",
    "trc_parser.py":        "trc_parser.py",
    "desktop_app.py":       "desktop/desktop_app.py",
}


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
        r = subprocess.run([sys.executable], env=env,
                           capture_output=True, timeout=90)
        return r.returncode == 0
    except Exception:
        return False


def _fetch_latest_code():
    """Download the live engine+UI into a cache dir and validate it before use.
    All-or-nothing: only swap in the cache if EVERY file downloaded, COMPILES
    (no syntax errors), and the engine IMPORTS cleanly. On any failure return
    None so we fall back to the bundled copies. Returns the cache dir or None."""
    import urllib.request
    staging = tempfile.mkdtemp(prefix="ss_fetch_")
    try:
        for local_name, repo_path in _LIVE_FILES.items():
            with urllib.request.urlopen(f"{_REPO_RAW}/{repo_path}",
                                        timeout=8) as resp:
                data = resp.read()
            # sanity: non-empty and looks like our python (guards against an
            # error page / truncated download being treated as code)
            if not data or b"def " not in data:
                raise ValueError(f"unexpected content for {repo_path}")
            # syntax check — catches the most common "bad push breaks everyone"
            compile(data, local_name, "exec")
            with open(os.path.join(staging, local_name), "wb") as fh:
                fh.write(data)
        # import smoke check — confirms the fetched engine actually loads
        if not _smoke_check_engine(staging):
            raise RuntimeError("fetched engine failed import smoke check")
        cache = os.path.join(os.path.expanduser("~"), ".secretsauce", "engine")
        os.makedirs(cache, exist_ok=True)
        for local_name in _LIVE_FILES:
            shutil.copy(os.path.join(staging, local_name),
                        os.path.join(cache, local_name))
        return cache
    except Exception:
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
    """If launched with SS_SMOKE_CHECK=<dir>, this is the isolated subprocess
    from _smoke_check_engine — just try to import the engine from <dir> and
    exit 0 (ok) or 1 (broken). Must run before any normal startup work."""
    folder = os.environ.get("SS_SMOKE_CHECK")
    if not folder:
        return False
    try:
        sys.path.insert(0, folder)
        import importlib
        for mod in ("sor_reader324802a", "trc_parser", "report", "report_sor"):
            importlib.import_module(mod)
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

    # Pull the latest code; use it if the fetch fully succeeded, else the
    # bundled copies. The cached desktop_app.py does sys.path.insert(0, HERE),
    # so its imports resolve to the freshly-fetched engine in the same dir.
    cache = _fetch_latest_code()
    if cache and os.path.exists(os.path.join(cache, "desktop_app.py")):
        script = os.path.join(cache, "desktop_app.py")
        os.environ["PYTHONPATH"] = cache + os.pathsep + os.environ.get("PYTHONPATH", "")
        os.environ["SS_ENGINE_SOURCE"] = "latest (auto-updated from the cloud)"
        print(f"[launcher] using auto-updated code from {cache}")
    else:
        script = os.path.join(_base_dir(), "desktop_app.py")
        os.environ["SS_ENGINE_SOURCE"] = "bundled (offline — using the built-in version)"
        print("[launcher] offline or fetch failed; using bundled code")

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
