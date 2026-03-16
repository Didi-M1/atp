"""pytest configuration: CLI options, session fixtures, shared helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atp.profile import load_profile, ProfileIncompleteError
from atp.reporter import PdfReporter
from atp.stability import (
    is_continuation,
    load_passed_tests,
    record_passed_test,
    clear_session,
)


# ──────────────────────────────────────────────
# CLI options
# ──────────────────────────────────────────────

def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--profile",
        action="store",
        required=True,
        metavar="NAME",
        help="Platform profile name, e.g. 'example_x86' (looks in profiles/ by default)",
    )
    parser.addoption(
        "--profiles-dir",
        action="store",
        default=None,
        metavar="DIR",
        help="Override directory that contains profile YAML files",
    )
    parser.addoption(
        "--pdf",
        action="store",
        default=None,
        metavar="PATH",
        help="Generate a PDF report at PATH (e.g. --pdf=report.pdf). "
             "Automatically generates an HTML report alongside it.",
    )


# ──────────────────────────────────────────────
# Early profile validation
# ──────────────────────────────────────────────

def pytest_configure(config: pytest.Config) -> None:
    """Validate the profile before any test is collected or run.
    Also registers PdfReporter when --pdf is requested.
    """
    # ── PDF reporter ────────────────────────────────────────────────────────
    try:
        pdf_path = config.getoption("--pdf")
    except ValueError:
        pdf_path = None

    if pdf_path:
        config.pluginmanager.register(PdfReporter(config, pdf_path), "atp-pdf-reporter")

    # ── Profile validation ──────────────────────────────────────────────────
    try:
        name: str = config.getoption("--profile")
    except ValueError:
        return  # option not registered yet (e.g. during --help)

    if not name:
        return

    try:
        profiles_dir_str: str | None = config.getoption("--profiles-dir")
    except ValueError:
        profiles_dir_str = None

    kwargs: dict = {}
    if profiles_dir_str:
        kwargs["profiles_dir"] = Path(profiles_dir_str)

    try:
        load_profile(name, **kwargs)
    except FileNotFoundError as exc:
        pytest.exit(f"\nProfile not found: {exc}", returncode=2)
    except ProfileIncompleteError as exc:
        pytest.exit(f"\nProfile incomplete:\n{exc}", returncode=2)


# ──────────────────────────────────────────────
# Test ordering and reboot-continuation hooks
# ──────────────────────────────────────────────

def pytest_sessionstart(session: pytest.Session) -> None:
    """On a fresh (non-continuation) run, clear any stale session state."""
    if not is_continuation():
        clear_session()


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Reorder and filter the collected test list.

    1. Move stability tests to run *last* so all other tests complete before
       the first reboot fires.  This means the reboot-continuation script only
       ever needs to resume stability — nothing else is left unrun.

    2. In a continuation session (reboot state file exists), mark tests that
       already passed as SKIP so they are not repeated unnecessarily.
    """
    # ── 1. Stability last ─────────────────────────────────────────────────
    stability = [i for i in items if "test_stability" in i.nodeid]
    others    = [i for i in items if "test_stability" not in i.nodeid]
    items[:] = others + stability

    # ── 2. Skip already-passed tests after a reboot ───────────────────────
    if not is_continuation():
        return
    passed = load_passed_tests()
    if not passed:
        return
    skip = pytest.mark.skip(reason="already passed before reboot")
    for item in items:
        if item.nodeid in passed:
            item.add_marker(skip)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Persist each passing test ID so it can be skipped after a reboot."""
    if report.when == "call" and report.passed:
        record_passed_test(report.nodeid)


# ──────────────────────────────────────────────
# Session-scoped fixtures
# ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def profile(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Load and return the merged platform profile dict."""
    name: str = request.config.getoption("--profile")
    profiles_dir_str: str | None = request.config.getoption("--profiles-dir")
    kwargs: dict = {}
    if profiles_dir_str:
        kwargs["profiles_dir"] = Path(profiles_dir_str)
    return load_profile(name, **kwargs)


@pytest.fixture(scope="session")
def profile_name(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--profile")


@pytest.fixture(scope="session")
def profiles_dir(request: pytest.FixtureRequest) -> str | None:
    return request.config.getoption("--profiles-dir")


# ──────────────────────────────────────────────
# Helpers (import in test files as needed)
# ──────────────────────────────────────────────

def skip_unless(condition: bool, reason: str) -> None:
    """Call pytest.skip unless *condition* is True."""
    if not condition:
        pytest.skip(reason)
