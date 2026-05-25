import logging
import time
import uuid

import pytest

from conftest import PaymentApiClient, reset_api

log = logging.getLogger(__name__)


def _uid(prefix):
    """Short unique id for idempotency keys."""
    return f"{prefix}-{uuid.uuid4().hex}"


def _check_json(resp):
    """Make sure we got JSON back, return parsed body."""
    ct = resp.headers.get("content-type", "")
    assert "application/json" in ct, f"expected json, got {ct}"
    return resp.json()


def create_payment(client, *, amount=25.0, recipient="OK_recipient", unique=None):
    payload = {
        "amount": amount,
        "uniqueId": unique or _uid("payment"),
        "recipient": recipient,
    }
    return client.post("/payments", json=payload, note=f"create payment {recipient}")


def _login_new_user(api, user_factory, prefix):
    """Register + login helper, returns authenticated client."""
    username, password = user_factory(prefix)
    resp = api.post(
        "/login",
        json={"username": username, "password": password},
        note=f"login for {prefix} client",
    )
    assert resp.status_code == 200, f"login failed: {resp.status_code}"
    return PaymentApiClient(token=resp.json()["token"])


def poll_until_done(client, payment_id, *, timeout=8.0, interval=0.75):
    """Poll payment status until terminal state or timeout."""
    deadline = time.monotonic() + timeout
    seen = []

    while time.monotonic() < deadline:
        resp = client.get(
            f"/payments/{payment_id}",
            note=f"poll payment {payment_id}",
        )
        assert resp.status_code == 200
        status = _check_json(resp)["status"]
        seen.append(status)
        if status in ("completed", "failed"):
            return status, seen, resp
        time.sleep(interval)

    pytest.fail(f"payment {payment_id} stuck, saw: {seen}")


# Basic health

class TestHealthAndLogin:

    def test_health_check(self, api):
        resp = api.get("/health", note="health check")

        assert resp.status_code == 200
        assert _check_json(resp) == {"status": "healthy"}

    def test_login_happy_path(self, api, user_factory):
        reset_api()
        username, password = user_factory("login")
        resp = api.post(
            "/login",
            json={"username": username, "password": password},
            note="valid @test.me login",
        )

        body = _check_json(resp)
        assert resp.status_code == 200
        assert body["token"]

    @pytest.mark.parametrize("payload", [
        {"username": "person@example.com", "password": "somepassword"},
        {"username": "not-an-email", "password": "somepassword"},
        {"username": "x" * 93 + "@test.me", "password": "somepassword"},
        {"username": "valid-format@test.me", "password": "wrongpassword"},
        {"username": "", "password": "somepassword"},
        {"password": "somepassword"},   # missing username entirely
    ])
    def test_login_rejects_bad_input(self, api, payload):
        reset_api()
        resp = api.post("/login", json=payload, note=f"invalid login {payload}")

        assert resp.status_code in {400, 401, 403, 422}
        _check_json(resp)

    def test_account_lockout_after_failed_logins(self, api):
        """5 wrong passwords -> account locked for a few seconds."""
        reset_api()
        username = f"locked-{uuid.uuid4().hex}@test.me"

        # burn through attempts
        for i in range(5):
            resp = api.post(
                "/login",
                json={"username": username, "password": f"wrong-{i}"},
                note="consecutive failed login",
            )
            assert resp.status_code in {400, 401, 403, 422, 429}

        # should be locked now
        locked = api.post(
            "/login",
            json={"username": username, "password": "somepassword"},
            note="valid login during lockout window",
        )
        assert locked.status_code in {401, 403, 423, 429}

        # wait for lockout to expire, then retry
        time.sleep(5.5)
        recovered = api.post(
            "/login",
            json={"username": username, "password": "somepassword"},
            note="valid login after lockout expires",
        )
        assert recovered.status_code == 200
        assert _check_json(recovered)["token"]
        reset_api()


# Payments CRUD tests

class TestPayments:

    def test_full_payment_lifecycle(self, authenticated_client):
        resp = create_payment(authenticated_client, amount=250.01)
        body = _check_json(resp)

        assert resp.status_code == 201
        assert body["status"] == "created"
        assert uuid.UUID(body["id"])   # valid uuid?

        final, seen, _ = poll_until_done(
            authenticated_client, body["id"], timeout=5,
        )
        assert final == "completed"
        assert "in_progress" in seen

    def test_duplicate_unique_id_gives_409(self, authenticated_client):
        dup = _uid("duplicate")
        first = create_payment(authenticated_client, amount=42.0, unique=dup)
        second = create_payment(authenticated_client, amount=42.0, unique=dup)

        assert first.status_code == 201
        assert second.status_code == 409
        _check_json(second)

    @pytest.mark.parametrize("payload, ok_statuses", [
        ({"amount": 0, "uniqueId": "zero", "recipient": "OK_recipient"}, {400, 422}),
        ({"amount": -1, "uniqueId": "negative", "recipient": "OK_recipient"}, {400, 422}),
        ({"amount": 10000.01, "uniqueId": "too-large", "recipient": "OK_recipient"}, {400, 402, 422}),
        ({"amount": 10, "uniqueId": "missing-recipient"}, {400, 422}),
        ({"amount": 10, "uniqueId": "long-recipient", "recipient": "R" * 101}, {400, 422}),
    ])
    def test_bad_payment_payloads(self, authenticated_client, payload, ok_statuses):
        payload["uniqueId"] = _uid(payload["uniqueId"])
        resp = authenticated_client.post(
            "/payments", json=payload,
            note=f"invalid payment payload {payload}",
        )
        assert resp.status_code in ok_statuses
        _check_json(resp)

    def test_recipient_prefix_behaviors(self, authenticated_client, api, user_factory):
        """INVALID_, FAKE_, FAIL_, SLOW_ prefixes trigger different server behavior."""
        invalid = create_payment(authenticated_client, recipient="INVALID_bad")
        fake = create_payment(authenticated_client, recipient="FAKE_missing")

        # need fresh users for FAIL/SLOW to avoid hitting rate limits on same user
        failing_client = _login_new_user(api, user_factory, "fail-prefix")
        slow_client = _login_new_user(api, user_factory, "slow-prefix")
        failing = create_payment(failing_client, amount=25.0, recipient="FAIL_later")
        slow = create_payment(slow_client, amount=1.0, recipient="SLOW_but_valid")

        assert invalid.status_code in {400, 422}
        assert fake.status_code in {400, 404, 422}
        assert failing.status_code == 201
        assert slow.status_code == 201

        failed_status, failed_seen, _ = poll_until_done(
            failing_client, failing.json()["id"], timeout=5,
        )
        assert failed_status == "failed"
        assert "in_progress" in failed_seen

        t0 = time.monotonic()
        slow_status, slow_seen, _ = poll_until_done(
            slow_client, slow.json()["id"], timeout=7,
        )
        elapsed = time.monotonic() - t0

        assert slow_status == "completed"
        assert "in_progress" in slow_seen
        assert elapsed >= 2.5   # SLOW_ prefix adds artificial delay

    def test_lookup_needs_valid_uuid_and_owner(self, authenticated_client, api, user_factory):
        created = create_payment(authenticated_client, amount=15.0)
        payment_id = created.json()["id"]

        # garbage uuid -> should fail
        bad = authenticated_client.get(
            "/payments/not-a-uuid",
            note="payment lookup with invalid UUID",
        )
        assert bad.status_code in {400, 404, 422}
        _check_json(bad)

        # different user trying to access someone else's payment
        username, password = user_factory("other-owner")
        login_resp = api.post(
            "/login",
            json={"username": username, "password": password},
            note="login as second user",
        )
        other = PaymentApiClient(token=login_resp.json()["token"])
        wrong_owner = other.get(
            f"/payments/{payment_id}",
            note="payment lookup by wrong user",
        )
        assert wrong_owner.status_code == 404
        _check_json(wrong_owner)

    def test_no_auth_means_no_payment(self, api):
        resp = api.post(
            "/payments",
            json={"amount": 10, "uniqueId": _uid("unauth"), "recipient": "OK_recipient"},
            note="create payment without bearer token",
        )
        assert resp.status_code in {401, 403}
        _check_json(resp)



@pytest.mark.rate_limit
class TestRateLimits:

    def test_per_user_rate_limit(self, api, user_factory):
        reset_api()
        client = _login_new_user(api, user_factory, "user-rate")

        statuses = []
        for _ in range(14):
            resp = client.get(
                f"/payments/{uuid.uuid4()}",
                note="rapid authenticated payment lookups for user rate limit",
            )
            statuses.append(resp.status_code)
            if resp.status_code == 429:
                break

        assert 429 in statuses, f"never got 429, saw: {set(statuses)}"

    def test_login_ip_rate_limit(self, api):
        reset_api()
        statuses = []

        for _ in range(35):
            resp = api.post(
                "/login",
                json={
                    "username": f"ip-rate-{uuid.uuid4().hex}@test.me",
                    "password": "wrongpassword",
                },
                note="rapid failed logins from same IP",
            )
            statuses.append(resp.status_code)
            if resp.status_code == 429:
                break

        assert 429 in statuses, f"never got 429, saw: {set(statuses)}"

    def test_single_user_overload(self, api, user_factory):
        """Hammering from one user should eventually trigger 429 or 503."""
        reset_api()
        client = _login_new_user(api, user_factory, "overload")

        statuses = []
        for _ in range(58):
            resp = client.get(
                f"/payments/{uuid.uuid4()}",
                note="single user overload probe",
            )
            statuses.append(resp.status_code)

        assert any(s in {429, 503} for s in statuses), \
            f"expected throttling, saw: {set(statuses)}"

    def test_global_rate_limit(self, api, user_factory):
        """Multiple users hitting the API concurrently should trigger global limit."""
        reset_api()
        clients = []

        for _ in range(5):
            u, p = user_factory("global-rate")
            login = api.post(
                "/login",
                json={"username": u, "password": p},
                note="login before global rate limit probe",
            )
            if login.status_code != 200:
                break
            clients.append(PaymentApiClient(token=login.json()["token"]))

        assert clients, "couldn't create any users for global rate limit test"

        statuses = []
        for i in range(105):
            c = clients[i % len(clients)]
            resp = c.get(
                f"/payments/{uuid.uuid4()}",
                note="cross-user global rate limit probe",
            )
            statuses.append(resp.status_code)
            if resp.status_code in {429, 503}:
                break

        assert any(s in {429, 503} for s in statuses), \
            f"expected global throttle, saw: {set(statuses)}"
        reset_api()
