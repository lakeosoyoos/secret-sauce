"""Error-reporting guard tests (report.report_error).

Reporting must be SAFE: a no-op when no webhook is configured, deduped to one
message per signature per hour, and it must NEVER raise (a reporting hiccup must
not break a tech's run). The webhook URL itself is baked into the .exe at build
time from a CI secret — never in source — so these tests use a dummy/unreachable
URL and assert the decision logic via the in-process dedup table.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
sys.path.insert(0, DESKTOP)

import report as R   # noqa: E402


def test_no_webhook_is_a_silent_noop():
    os.environ.pop("SS_ERROR_WEBHOOK", None)
    R._ERR_LAST.clear()
    R.report_error("unit", ValueError("boom"))   # must not raise
    assert R._ERR_LAST == {}                      # nothing queued without a webhook


def test_records_then_dedups_within_the_hour(monkeypatch):
    # Unreachable URL: the background send fails silently; we assert the DECISION
    # logic (record once, suppress the repeat) via the in-process table.
    monkeypatch.setenv("SS_ERROR_WEBHOOK", "http://127.0.0.1:9/none")
    R._ERR_LAST.clear()
    R.report_error("unit", ValueError("boom"), {"files": 3})
    assert len(R._ERR_LAST) == 1
    first = list(R._ERR_LAST.values())[0]
    R.report_error("unit", ValueError("boom"), {"files": 3})   # same signature
    assert list(R._ERR_LAST.values())[0] == first              # not re-sent


def test_distinct_errors_each_record(monkeypatch):
    monkeypatch.setenv("SS_ERROR_WEBHOOK", "http://127.0.0.1:9/none")
    R._ERR_LAST.clear()
    R.report_error("unit", ValueError("a"))
    R.report_error("unit", KeyError("b"))
    assert len(R._ERR_LAST) == 2


def test_never_raises_on_weird_context(monkeypatch):
    monkeypatch.setenv("SS_ERROR_WEBHOOK", "http://127.0.0.1:9/none")
    R._ERR_LAST.clear()
    R.report_error("unit", ValueError("x"), context={"obj": object()})
    # passes iff no exception escaped
