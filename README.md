# Payment Service API QA Automation

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
pytest -s
```

To run everything except the heavier rate-limit probes:

```bash
pytest -s -m "not rate_limit"
```

## Pytest-html report(additional)

on every 

```bash
pytest -s
```
the .html report is generated containing the cases, amount of scenarios and points(total)

## Discovered Scenarios

Latest local run: 28 unique scenarios, 90 total points.

### Latest local run
**28** unique scenarios, **90** total points.

| Scenario def | Points | Case |
| :--- | :---: | :--- |
| `account_locked` | 3 | Attempt a valid login during the 5-second lockout window after 5 failed logins |
| `concurrency_duplicate` | 12 | Reuse the same `uniqueId` in a second `POST /payments` request |
| `failed_recipient` | 2 | Create a payment with a `FAIL_` recipient |
| `happy_path_login` | 1 | `POST /login` with valid `@test.me` credentials |
| `happy_path_payment` | 1 | `POST /payments` with valid auth and payload |
| `insufficient_funds` | 2 | Create a payment above the allowed amount boundary |
| `invalid_amount` | 2 | Create a payment with amount `0` |
| `invalid_credentials` | 2 | `POST /login` with a valid username and wrong password |
| `invalid_payment_id` | 2 | `GET /payments/not-a-uuid` |
| `invalid_recipient` | 3 | Create a payment with an `INVALID_` recipient |
| `invalid_username_format` | 2 | `POST /login` with a malformed email |
| `load_test_detected` | 11 | Send more than 50 requests in 10 seconds from one authenticated user |
| `missing_auth_token` | 2 | `POST /payments` without an `Authorization` header |
| `negative_amount` | 2 | Create a payment with a negative amount |
| `payment_not_found` | 2 | Lookup a random valid UUID that does not exist |
| `payment_status_completed` | 1 | Poll a normal payment until it reaches `completed` |
| `payment_status_created` | 1 | Poll a newly created payment while it is still `created` |
| `payment_status_failed` | 1 | Poll a `FAIL_` payment until it reaches `failed` |
| `payment_status_in_progress` | 1 | Poll a payment while it is `in_progress` |
| `payment_unauthorized` | 6 | Try to fetch another user's payment |
| `rate_limit_global` | 8 | Send requests across multiple authenticated users until the global limit is hit |
| `rate_limit_login` | 5 | Rapidly submit failed login attempts from the same IP |
| `rate_limit_payment` | 5 | Rapidly call authenticated payment lookup for one user |
| `recipient_too_long` | 2 | Create a payment with a recipient longer than 100 characters |
| `slow_recipient` | 2 | Create and poll a payment with a `SLOW_` recipient |
| `unknown_recipient` | 3 | Create a payment with a `FAKE_` recipient |
| `unknown_username_domain` | 4 | `POST /login` with a non-`@test.me` email |
| `username_too_long` | 2 | `POST /login` with a username longer than 100 characters |
| **Total** | **90** | |
