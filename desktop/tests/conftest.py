"""Shared scaffolding for the Secret Sauce desktop test suite.

CI runs pytest with working-directory `desktop`, so tests import the engine
modules (report / report_sor) and drive the Streamlit app (desktop_app.py) that
live one level up in `desktop/`. This conftest exports the few constants every
test needs plus a `run_streamlit` helper that builds a streamlit AppTest with
sys.path wired to the desktop/ dir.
"""
import os
import sys

import pytest

# desktop/tests/conftest.py  ->  DESKTOP_DIR = desktop/
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DESKTOP_DIR = os.path.dirname(TESTS_DIR)
APP_PATH = os.path.join(DESKTOP_DIR, "desktop_app.py")

FIXTURE_DIR = os.path.join(TESTS_DIR, "fixtures")
FIXTURE_SOR_DIR = os.path.join(FIXTURE_DIR, "sor")
FIXTURE_JSON_DIR = os.path.join(FIXTURE_DIR, "json")

# desktop_app.py does `from report import ...` / `from report_sor import ...`,
# which only resolves if desktop/ is importable. The app itself inserts HERE
# onto sys.path at import time, but AppTest compiles the script in-process, so
# we make sure desktop/ is on sys.path before any test runs.
if DESKTOP_DIR not in sys.path:
    sys.path.insert(0, DESKTOP_DIR)


def run_streamlit(default_timeout=60, **kwargs):
    """Build a streamlit AppTest for desktop_app.py with sys.path wired.

    Returns the (un-run) AppTest so the caller can seed session_state before
    calling .run(). Extra kwargs are forwarded to AppTest.from_file.
    """
    from streamlit.testing.v1 import AppTest

    if DESKTOP_DIR not in sys.path:
        sys.path.insert(0, DESKTOP_DIR)
    return AppTest.from_file(APP_PATH, default_timeout=default_timeout, **kwargs)


# Expose the constants as importable names AND as pytest fixtures so tests can
# take either style.
@pytest.fixture
def app_path():
    return APP_PATH


@pytest.fixture
def fixture_sor_dir():
    return FIXTURE_SOR_DIR


@pytest.fixture
def fixture_json_dir():
    return FIXTURE_JSON_DIR
