import logging
import os
from collections import namedtuple

import pytest
import requests

log = logging.getLogger(__name__)

BASE_URL = os.getenv(
    "PAYMENT_API_BASE_URL",
    "https://payment-testing-983614458473.europe-north1.run.app",
)
ADMIN_KEY = os.getenv("PAYMENT_ADMIN_KEY", "admin-reset-key-qa-2026")

# scenario trackin, keep best reward per code
Scenario = namedtuple("Scenario", ["code", "reward", "trigger", "status_code"])
SCENARIOS = {}


def record_response(resp, trigger):
    """Grab X-Scenario-* headers if present and track the best reward."""
    code = resp.headers.get("X-Scenario-Code")
    if not code:
        return

    try:
        reward = int(resp.headers.get("X-Scenario-Reward", 0))
    except (ValueError, TypeError):
        reward = 0

    prev = SCENARIOS.get(code)
    if prev is None or reward > prev.reward:
        SCENARIOS[code] = Scenario(code, reward, trigger, resp.status_code)
        log.debug("scenario %s  reward=%d  status=%d", code, reward, resp.status_code)


class PaymentApiClient:
    """Thin wrapper around requests for the payment API."""

    def __init__(self, base_url=BASE_URL, token=None):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.token = token

    # TODO: maybe add retry logic here for flaky network
    def request(self, method, path, *, note=None, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")

        url = f"{self.base_url}{path}"
        log.info("%s %s", method, url)

        resp = self.session.request(
            method, url, headers=headers, timeout=15, **kwargs,
        )
        record_response(resp, note or f"{method} {path}")

        log.info("  -> %d (%d bytes)", resp.status_code, len(resp.content))
        return resp

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


def reset_api():
    """Reset server state via admin endpoint."""
    log.info("resetting API state...")
    resp = requests.post(
        f"{BASE_URL.rstrip('/')}/admin/reset",
        headers={"X-Admin-Key": ADMIN_KEY},
        timeout=15,
    )
    record_response(resp, "POST /admin/reset")
    resp.raise_for_status()
    return resp


#fixtures

@pytest.fixture(scope="session", autouse=True)
def clean_environment_before_run():
    reset_api()


@pytest.fixture
def api():
    return PaymentApiClient()


@pytest.fixture
def user_factory():
    _counter = 0

    def _make(prefix="qa"):
        nonlocal _counter
        _counter += 1
        return f"{prefix}-{_counter}-{os.urandom(4).hex()}@test.me", "somepassword"

    return _make


@pytest.fixture
def authenticated_client(api, user_factory):
    reset_api()
    username, password = user_factory("auth")
    resp = api.post(
        "/login",
        json={"username": username, "password": password},
        note="valid login for authenticated test client",
    )
    assert resp.status_code == 200
    return PaymentApiClient(token=resp.json()["token"])


# --------------- hooks ---------------

def pytest_sessionfinish(session, exitstatus):
    total = sum(s.reward for s in SCENARIOS.values())
    print("\n\n=== Payment API Scenario Score ===")
    print(f"Unique scenarios discovered: {len(SCENARIOS)}")
    print(f"Total score: {total}")

    if not SCENARIOS:
        print("No scenario headers were returned.")
        return

    print("\nScenario code | Points | Status | Trigger method")
    print("--- | ---: | ---: | ---")
    for code in sorted(SCENARIOS):
        s = SCENARIOS[code]
        print(f"{s.code} | {s.reward} | {s.status_code} | {s.trigger}")


# --- pytest-html integration (optional, only if plugin loaded) ---

def _is_html_plugin_active(config):
    return config.pluginmanager.hasplugin("html")


def pytest_configure(config):
    """Register extra metadata for the HTML report when pytest-html is installed."""
    if not _is_html_plugin_active(config):
        return
    config.stash.setdefault("_scenario_results", {})


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_summary(prefix, summary, postfix, session):
    """Inject scenario scoreboard into the HTML report header."""
    if not SCENARIOS:
        return

    total = sum(s.reward for s in SCENARIOS.values())

    rows = []
    for code in sorted(SCENARIOS):
        s = SCENARIOS[code]
        rows.append(
            f"<tr><td>{s.code}</td><td>{s.reward}</td>"
            f"<td>{s.status_code}</td><td>{s.trigger}</td></tr>"
        )

    table_html = (
        f"<h2>Scenario Score: {total} ({len(SCENARIOS)} unique)</h2>"
        "<table border='1' cellpadding='4' style='border-collapse:collapse'>"
        "<tr><th>Scenario</th><th>Points</th><th>Status</th><th>Trigger</th></tr>"
        + "\n".join(rows)
        + "</table>"
    )
    prefix.extend([table_html])
