"""PDF (and static HTML) report generator for ATP test results.

Collects test results via pytest hooks and produces a fully static HTML page
(no JavaScript) that weasyprint can render faithfully to PDF.

pytest-html is kept for the --html flag (interactive browser report);
this module handles --pdf independently.
"""
from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

# ──────────────────────────────────────────────
# HTML template
# ──────────────────────────────────────────────

_CSS = """
@page { size: A4; margin: 18mm 14mm; }

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 11px;
    color: #222;
    background: #fff;
}
h1 { font-size: 18px; font-weight: bold; margin-bottom: 5px; }
.meta { color: #555; font-size: 10px; margin-bottom: 18px; line-height: 1.9; }
.meta strong { color: #222; }

/* ── summary boxes — table layout so WeasyPrint renders reliably ── */
.summary { border-collapse: separate; border-spacing: 8px 0;
           margin-bottom: 22px; }
.box {
    display: inline-block;
    padding: 10px 16px; border-radius: 5px;
    text-align: center; min-width: 80px;
    margin-right: 8px; margin-bottom: 8px;
    vertical-align: top;
}
.box .count { font-size: 24px; font-weight: bold; line-height: 1.1; }
.box .label { font-size: 9px; text-transform: uppercase;
              margin-top: 3px; letter-spacing: .4px; }
.box-total   { background: #e9ecef; color: #343a40; }
.box-passed  { background: #d4edda; color: #155724; }
.box-failed  { background: #f8d7da; color: #721c24; }
.box-skipped { background: #fff3cd; color: #856404; }
.box-error   { background: #f8d7da; color: #721c24; }

/* ── results table ── */
h2 { font-size: 12px; font-weight: bold; margin-bottom: 6px; color: #333; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
thead tr { background: #343a40; color: #fff; }
th { padding: 7px 8px; text-align: left; font-size: 10px; font-weight: bold; }
td { padding: 5px 8px; border-bottom: 1px solid #dee2e6; vertical-align: top; }

/* prevent a row from being split across two pages */
tr { break-inside: avoid; page-break-inside: avoid; }

tr.even { background: #f8f9fa; }
.status { font-weight: bold; white-space: nowrap; font-size: 10px; }
.passed  { color: #28a745; }
.failed  { color: #dc3545; }
.skipped { color: #856404; }
.error   { color: #dc3545; }
.dur { text-align: right; white-space: nowrap; color: #666; font-size: 10px; }
.note { color: #555; font-style: italic; font-size: 9.5px; line-height: 1.5; }
.testid { font-size: 9.5px; line-height: 1.5; word-break: break-word;
          overflow-wrap: break-word; }
.testfile { color: #666; }
.testname { color: #111; }
"""

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ATP Report — {profile}</title>
  <style>{css}</style>
</head>
<body>
  <h1>ATP Test Report</h1>
  <div class="meta">
    <strong>Profile:</strong> {profile} &nbsp;&nbsp;
    <strong>Platform:</strong> {uname} &nbsp;&nbsp;
    <strong>Generated:</strong> {date} &nbsp;&nbsp;
    <strong>Duration:</strong> {total_duration}
  </div>

  <div class="summary-row">
    <span class="box box-total">
      <div class="count">{n_total}</div><div class="label">Total</div>
    </span><span class="box box-passed">
      <div class="count">{n_passed}</div><div class="label">Passed</div>
    </span><span class="box box-failed">
      <div class="count">{n_failed}</div><div class="label">Failed</div>
    </span><span class="box box-skipped">
      <div class="count">{n_skipped}</div><div class="label">Skipped</div>
    </span><span class="box box-error">
      <div class="count">{n_error}</div><div class="label">Errors</div>
    </span>
  </div>

  <h2>Results</h2>
  <table>
    <thead>
      <tr>
        <th style="width:4%">#</th>
        <th style="width:46%">Test</th>
        <th style="width:10%">Status</th>
        <th style="width:8%">Duration</th>
        <th style="width:32%">Notes</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
</body>
</html>
"""

_ROW = """\
      <tr class="{row_class}">
        <td>{idx}</td>
        <td class="testid"><span class="testfile">{file}</span><br><span class="testname">{classmethod}</span></td>
        <td><span class="status {status_class}">{status}</span></td>
        <td class="dur">{duration}</td>
        <td class="note">{note}</td>
      </tr>"""


def _split_nodeid(nodeid: str) -> tuple[str, str]:
    """Split 'tests/test_foo.py::TestBar::test_baz' into (file, 'TestBar :: test_baz')."""
    # Strip leading tests/ directory
    parts = nodeid.split("::", 1)
    file_part = parts[0].removeprefix("tests/")
    rest = parts[1].replace("::", " :: ") if len(parts) > 1 else ""
    return file_part, rest


# ──────────────────────────────────────────────
# Result collector
# ──────────────────────────────────────────────

class PdfReporter:
    """pytest plugin that collects results and writes a PDF at session end."""

    def __init__(self, config: pytest.Config, pdf_path: str) -> None:
        self.config = config
        self.pdf_path = Path(pdf_path)
        self._results: list[dict[str, Any]] = []
        self._session_start = datetime.now()

    # ── hooks ────────────────────────────────────────────────────────────────

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo) -> None:
        outcome = yield
        report: pytest.TestReport = outcome.get_result()

        # One entry per test: "call" phase for pass/fail, "setup" phase for skip
        if report.when == "call" or (report.when == "setup" and report.skipped):
            note = ""
            if report.skipped:
                raw = report.longrepr
                if isinstance(raw, tuple) and len(raw) == 3:
                    note = str(raw[2]).removeprefix("Skipped: ")
                else:
                    note = str(raw)
            elif report.failed:
                lines = str(report.longrepr).splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line and not line.startswith("_") and not line.startswith("="):
                        note = line
                        break

            self._results.append({
                "nodeid": report.nodeid,
                "outcome": report.outcome,
                "duration": report.duration,
                "note": note,
            })

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        html = self._render_html()
        try:
            from weasyprint import HTML
            HTML(string=html).write_pdf(str(self.pdf_path))
            print(f"\nPDF report → {self.pdf_path}")
        except Exception as exc:
            print(f"\nPDF generation failed: {exc}")

    # ── rendering ────────────────────────────────────────────────────────────

    def _render_html(self) -> str:
        profile = self.config.getoption("--profile", default="unknown")
        total_secs = (datetime.now() - self._session_start).total_seconds()

        counts: dict[str, int] = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
        rows: list[str] = []

        for idx, r in enumerate(self._results, start=1):
            outcome = r["outcome"]
            if outcome == "passed":
                counts["passed"] += 1
                status_label, status_class = "PASSED", "passed"
            elif outcome == "failed":
                counts["failed"] += 1
                status_label, status_class = "FAILED", "failed"
            elif outcome == "skipped":
                counts["skipped"] += 1
                status_label, status_class = "SKIPPED", "skipped"
            else:
                counts["error"] += 1
                status_label, status_class = outcome.upper(), "error"

            dur = r["duration"]
            dur_str = f"{dur*1000:.0f} ms" if dur < 1 else f"{dur:.2f} s"

            file_part, classmethod_part = _split_nodeid(r["nodeid"])
            rows.append(_ROW.format(
                row_class="even" if idx % 2 == 0 else "odd",
                idx=idx,
                file=file_part,
                classmethod=classmethod_part,
                status=status_label,
                status_class=status_class,
                duration=dur_str,
                note=r["note"],
            ))

        n_total = len(self._results)
        dur_str = f"{total_secs:.1f} s" if total_secs >= 1 else f"{total_secs*1000:.0f} ms"

        return _HTML.format(
            css=_CSS,
            profile=profile,
            uname=platform.node(),
            date=self._session_start.strftime("%Y-%m-%d %H:%M:%S"),
            total_duration=dur_str,
            n_total=n_total,
            n_passed=counts["passed"],
            n_failed=counts["failed"],
            n_skipped=counts["skipped"],
            n_error=counts["error"],
            rows="\n".join(rows),
        )
