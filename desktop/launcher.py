"""
PyInstaller entry point for the packaged SecretSauce desktop app.

When frozen, PyInstaller unpacks our files to a temp dir (sys._MEIPASS).
This launcher boots Streamlit pointed at desktop_app.py and opens the
browser, so the tech just double-clicks SecretSauce.exe.
"""
import os
import sys
import time
import threading
import webbrowser


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


def main():
    _redirect_output_to_log()
    _silence_first_run_prompt()
    script = os.path.join(_base_dir(), "desktop_app.py")
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
