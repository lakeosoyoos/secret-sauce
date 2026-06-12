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
    # give Streamlit a few seconds to bind the port, then open the UI
    time.sleep(4)
    webbrowser.open("http://localhost:8501")


def main():
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
