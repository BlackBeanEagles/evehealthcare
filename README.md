# EVE Healthcare — Diagnostics Booking API

A backend for booking diagnostic tests at partner centres and paying for them
through a **simulated** payment provider with an **idempotent webhook**.

Django 4.2 · Django REST Framework · PostgreSQL · JWT · Redis · Celery · Docker · web frontend · 196 tests

---

## Contents

- [Quick start](#quick-start)
- [What it does](#what-it-does)
- [Frontend](#frontend)
- [API reference](#api-reference)
- [Walk through the whole flow](#walk-through-the-whole-flow-copy-paste)
- [Database design](#database-design)
- [How idempotency is guaranteed](#how-idempotency-is-guaranteed)
- [Edge cases handled](#edge-cases-handled)
- [Project layout](#project-layout)
- [Tests](#tests)
- [Configuration](#configuration)
- [Assumptions](#assumptions)
- [What I would do with more time](#what-i-would-do-with-more-time)

---

## Quick start

### With Docker (recommended — one command)

```bash
docker compose up --build
```

That starts Postgres, Redis, the API and a Celery worker; migrations run and
the catalogue is seeded automatically on boot.

- **App (frontend): <http://localhost:8000/>**
- API: <http://localhost:8000/api/v1/>
- Swagger UI: <http://localhost:8000/api/docs/>
- Health: <http://localhost:8000/health/>
- Admin: <http://localhost:8000/admin/>

Seeded accounts:

| Role | Email | Password |
| --- | --- | --- |
| Patient | `patient@example.com` | `DemoPass123!` |
| Staff / admin | `admin@example.com` | `AdminPass123!` |

### Without Docker

Requires Python 3.11+. Postgres is the production target, but SQLite works for
a quick look.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://eve:eve@localhost:5432/eve_diagnostics  # or sqlite:///dev.sqlite3
python manage.py migrate
python manage.py seed_catalog
python manage.py runserver
```

### Run the tests

```bash
pytest
```

No database server needed — the suite defaults to in-memory SQLite. To run the
identical suite against the real target:

```bash
DATABASE_URL=postgresql://eve:eve@localhost:5432/eve_diagnostics pytest
```

---

## What it does

A patient signs up, browses centres and the tests each one offers (**with that
centre's price**), books a slot, and pays. Payment is simulated: no real
gateway is integrated. The provider then calls back on a webhook, possibly more
than once, possibly out of order — and the booking must end up in exactly the
right state regardless.

```
signup/login ──▶ browse catalogue ──▶ POST /bookings/  (PENDING, price snapshotted)
                                              │
                                              ▼
                                      POST /payments/   ── simulated charge
                                              │
                          ┌───────────────────┴───────────────────┐
                          ▼                                       ▼
                   SUCCESS → CONFIRMED                     FAILED → FAILED
                          │                                       │
                          │                                  retry payment
                          ▼                                       │
             POST /payments/webhook/  ◀── provider callback ◀──────┘
             (re-asserts the outcome; replays change nothing)
```

---

## Frontend

A light-theme web app served by Django at `/`, so `docker compose up` gives you
the whole product at <http://localhost:8000/>. Log in with the demo patient
(there is a one-click "Fill in" button) and walk the full flow:

1. **Explore:** partner centres, filterable by city, plus a test search.
2. **Compare:** pick a test and see every centre that offers it, cheapest
   first, with the price spread drawn as a bar. This is the per-centre pricing
   model made visible.
3. **Book:** choose a day and a 30-minute slot inside the centre's opening
   hours. Slots inside the 30-minute lead window are disabled.
4. **Pay:** choose a method and the simulated outcome (approve, decline or
   random). Each attempt carries its own `Idempotency-Key`, so a double-click
   or a flaky retry can never charge twice. After a decline, "Try again" starts
   a fresh attempt.
5. **Track:** each booking shows its status, its full transition history
   (who changed it: you, the payment, or the provider webhook) and every
   payment attempt. You can cancel from there.

**Design.** A warm paper background, ink for anything you read, and a palette
of saturated abstract colours (coral, saffron, teal, lilac, sky). Every
centre, test and booking gets a generated abstract artwork seeded from its
identifier, so the same centre always has the same "face". Colour carries
identity; it never carries the text.

**Implementation.** Plain JavaScript with no framework and no build step, in
three files under `frontend/`. It is a thin client over the documented API:

- Same origin as the API, so no CORS configuration and no second container.
- JWT access and refresh tokens. An expired access token is refreshed once,
  transparently; logout blacklists the refresh token.
- Every API string passes through an escaping helper before it reaches
  `innerHTML`.
- Hash routing. Stale responses from a page you already left are discarded
  rather than painted over the current one.
- Native `<dialog>` for modals, radio-group chips for day, slot, method and
  outcome selection, keyboard focus rings, `prefers-reduced-motion`
  respected, and a layout that works at phone width.

What it deliberately does *not* do: the webhook needs the provider's HMAC
secret, which must never ship to a browser, so the idempotency demo stays on
the CLI (`send_test_webhook`). Tokens live in `localStorage`, which is fine for
a demo; production would use httpOnly cookies. See the improvements list.

---

## API reference

Full interactive docs at `/api/docs/`; machine-readable schema at `/api/schema/`.
Everything lives under `/api/v1/`. The two paths named literally in the brief,
`POST /payments/` and `POST /payments/webhook/`, are also mounted at the root
as aliases onto the same views.

### Auth

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/signup/` | — | Create an account |
| POST | `/api/v1/auth/login/` | — | Email + password → JWT pair |
| POST | `/api/v1/auth/refresh/` | — | Refresh → new access token |
| POST | `/api/v1/auth/logout/` | JWT | Blacklist a refresh token |
| GET/PATCH | `/api/v1/auth/me/` | JWT | Read or update your profile |

### Catalogue (read: any user · write: staff only)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/centres/` | List centres — `?city=&search=&test=&page=&page_size=` |
| GET | `/api/v1/centres/{id}/` | Centre detail, including its price list |
| GET | `/api/v1/centres/{id}/tests/` | Tests offered by that centre, with prices |
| GET | `/api/v1/tests/` | Test catalogue — `?search=&category=` |
| GET | `/api/v1/offerings/` | **Test-at-a-centre with a price** — `?test=&city=&max_price=&ordering=` |
| POST/PATCH/DELETE | the above | Staff only (403 otherwise) |

### Bookings (always scoped to the caller)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/bookings/` | Your bookings — `?status=&from=&to=&ordering=` |
| POST | `/api/v1/bookings/` | Book a test |
| GET | `/api/v1/bookings/{reference}/` | One booking, with its full status history |
| POST | `/api/v1/bookings/{reference}/cancel/` | Cancel it |

### Payments

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/v1/payments/` | JWT | Simulated charge. Honours `Idempotency-Key` |
| GET | `/api/v1/payments/` | JWT | Your payments — `?status=&booking=` |
| GET | `/api/v1/payments/{reference}/` | JWT | One payment |
| POST | `/api/v1/payments/webhook/` | HMAC signature | Provider callback — **idempotent** |

### Ops

`GET /health/` (database + cache probe), `/api/docs/`, `/api/redoc/`, `/api/schema/`.

### Error format

Every error uses one envelope with a stable machine-readable `code`:

```json
{
  "error": {
    "code": "invalid_state_transition",
    "message": "A booking that is CANCELLED cannot become CONFIRMED.",
    "details": { "from": "CANCELLED", "to": "CONFIRMED", "booking": "BKG-3F91A0C2D5" }
  },
  "request_id": "0f2c8d1a4b..."
}
```

`request_id` is echoed in the `X-Request-ID` response header and stamped on
every log line for that request.

| Status | When |
| --- | --- |
| 400 | Payload or query parameter is invalid |
| 401 | Missing/expired JWT, or a bad webhook signature |
| 403 | Authenticated but not allowed (e.g. a patient writing to the catalogue) |
| 404 | Unknown resource — **also** returned for resources owned by someone else |
| 409 | The resource exists but its state forbids this (already paid, cancelled, duplicate) |
| 429 | Rate limited |

---

## Walk through the whole flow (copy-paste)

```bash
BASE=http://localhost:8000

# 1. Log in as the seeded patient
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"patient@example.com","password":"DemoPass123!"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access"])')

# 2. Find the cheapest centre offering a complete blood count
curl -s "$BASE/api/v1/offerings/?test=CBC&ordering=price" -H "Authorization: Bearer $TOKEN"

# 3. Book it (use an offering id from the previous response)
curl -s -X POST $BASE/api/v1/bookings/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"offering": 1, "scheduled_for": "2026-10-15T10:00:00+05:30"}'
# → {"reference":"BKG-...","status":"PENDING","amount":"350.00", ...}

# 4. Pay for it. `outcome` forces the simulated result; omit it for random.
curl -s -X POST $BASE/payments/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-key-1' \
  -d '{"booking":"BKG-...","method":"UPI","outcome":"success"}'
# → 201 {"reference":"PAY-...","status":"SUCCESS","booking_status":"CONFIRMED"}

# Replaying the same Idempotency-Key returns 200 and the SAME payment.
```

### See the webhook idempotency for yourself

```bash
docker compose exec web python manage.py send_test_webhook --payment PAY-XXXXXXXX --times 3
```

```
delivery 1/3: HTTP 200 {'received': True, 'duplicate': False, 'result': 'already_success'}
delivery 2/3: HTTP 200 {'received': True, 'duplicate': True,  'result': 'already_success'}
delivery 3/3: HTTP 200 {'received': True, 'duplicate': True,  'result': 'already_success'}
```

One `WebhookEvent` row, one `Payment` row, one booking transition. Add
`--print-only` to get a signed `curl` command instead of sending.

---

## Database design

```mermaid
erDiagram
    USER ||--o{ BOOKING : places
    DIAGNOSTIC_CENTRE ||--o{ CENTRE_TEST : offers
    DIAGNOSTIC_TEST   ||--o{ CENTRE_TEST : "is offered as"
    CENTRE_TEST ||--o{ BOOKING : "is booked as"
    BOOKING ||--o{ PAYMENT : "is paid by"
    BOOKING ||--o{ BOOKING_STATUS_CHANGE : "audited by"
    PAYMENT ||--o{ WEBHOOK_EVENT : "settled by"
```

| Table | Purpose |
| --- | --- |
| `users` | Email-as-username. Deactivated, never deleted (`PROTECT` from bookings). |
| `diagnostic_centres` | Name, address, city, pincode, opening hours, active flag. |
| `diagnostic_tests` | Master catalogue: code, name, category, prep, report ETA. |
| `centre_tests` | **The join that carries the price.** Unique on `(centre, test)`. |
| `bookings` | Patient, offering, slot, **amount snapshot**, status, audit timestamps. |
| `booking_status_changes` | Append-only log of every transition and what caused it. |
| `payments` | Amount, status, method, provider ref, idempotency key, raw payload. |
| `webhook_events` | Every inbound delivery, stored before it is acted on. |

### Three decisions worth defending

**1. Price lives on `centre_tests`, not on `diagnostic_tests`.**
A CBC is ₹299 in Koramangala and ₹420 in Andheri. A `price` column on the test
would be wrong the moment a second centre exists. The booking references the
offering, so it carries both the centre and the test through one foreign key
with no denormalised fields that can drift.

**2. `bookings.amount` is a snapshot, not a lookup.**
Copied from `centre_tests.price` at creation. Repricing an offering must never
retroactively change what a patient agreed to pay. There is a test for exactly
this.

**3. Correctness is enforced in the schema, not only in Python.**
Application checks lose races; unique indexes do not.

| Constraint | What it prevents |
| --- | --- |
| `uniq_test_per_centre` on `(centre, test)` | Two prices for the same test at one centre |
| `uniq_active_booking_per_slot` — partial, `status IN (PENDING, CONFIRMED)` | Double-booking a slot, while still allowing a cancelled slot to be rebooked |
| `uniq_open_payment_per_booking` — partial, `status IN (PENDING, SUCCESS)` | Double-charging, while still allowing a retry after a decline |
| `uniq_payment_idempotency_key` — partial, `WHERE key IS NOT NULL` | A retried `POST /payments/` charging twice |
| `webhook_events.event_id` unique | A redelivered webhook being processed twice |
| `payment_amount_positive`, `booking_amount_non_negative` | Nonsensical money |
| `PROTECT` on booking → user / offering | Deleting a row that financial records depend on |

Indexes back the queries that exist: `(user, status)` and `(status, scheduled_for)`
on bookings, `(test, price)` on offerings, `(booking, status)` on payments.

### Booking state machine

```mermaid
stateDiagram-v2
    [*] --> PENDING: booking created
    PENDING --> CONFIRMED: payment succeeded
    PENDING --> FAILED: payment declined
    PENDING --> CANCELLED: patient cancels
    FAILED --> PENDING: new payment attempt
    FAILED --> CANCELLED: patient cancels
    CONFIRMED --> CANCELLED: cancelled before cutoff
    CANCELLED --> [*]
```

The table lives in one place, `apps/bookings/services.py::ALLOWED_TRANSITIONS`,
and every state change goes through `transition_booking()`. A view never
assigns `booking.status` directly. All sixteen ordered pairs are asserted in
`tests/test_state_machine.py`.

---

## How idempotency is guaranteed

This is the part of the brief that actually matters, so here is the reasoning
in full.

**1. Store first, act second.** The webhook view verifies the signature, then
inserts a `WebhookEvent` row, and only then processes it. A crash mid-processing
cannot lose the event; it stays `RECEIVED`/`FAILED` and is picked up by the
retry task or `manage.py replay_failed_webhooks`.

**2. The duplicate check is a unique index, not an `if`.**

```python
try:
    with transaction.atomic():
        event = WebhookEvent.objects.create(event_id=event_id, ...)
    return event, True
except IntegrityError:
    return WebhookEvent.objects.get(event_id=event_id), False   # duplicate
```

`filter(...).exists()` followed by `create()` has a window that two simultaneous
deliveries both pass. Letting the database arbitrate has no such window.

**3. Applying an outcome is idempotent and first-outcome-wins.** Both the
synchronous charge and the webhook converge on `apply_payment_outcome()`, which
locks the payment row (`SELECT … FOR UPDATE`) and then:

| Situation | Behaviour |
| --- | --- |
| Payment already has this status | No-op, returns `already_success` / `already_failed` |
| Payment already terminal, event asserts the other outcome | **Refused** and logged at WARNING; a settled payment never flips |
| Event asserts a non-terminal status | Ignored |
| Otherwise | Settle the payment, then transition the booking |

**4. Transitioning to the current status is a no-op, not an error.** That is
what lets a replay pass through the state machine without raising.

**5. Unprocessable ≠ retryable.** An unknown payment, an unsupported event type
or an amount mismatch is stored, marked `IGNORED` and answered **200** — a 4xx
would make the provider retry something that can never succeed. Genuine
processing errors return **500**, which is the signal to retry.

**6. `POST /payments/` is idempotent too**, via the `Idempotency-Key` header.
Replaying a key returns the original payment with 200 instead of 201, and never
charges again.

**Proof.** `tests/test_webhooks.py` delivers one event five times and asserts:
exactly one `WebhookEvent`, exactly one `Payment`, exactly one
`PENDING → CONFIRMED` transition. It also covers two distinct events asserting
the same outcome, a contradictory late event, and a success arriving *after* the
patient cancelled.

### Webhook signature

Deliveries are signed `X-Webhook-Signature: t=<unix>,v1=<hmac-sha256 of "<t>.<body>">`
with a shared secret, verified with `hmac.compare_digest`, and rejected outside a
300-second window. An unauthenticated public endpoint that flips bookings to
CONFIRMED is otherwise an open door. Set `PAYMENT_WEBHOOK_REQUIRE_SIGNATURE=false`
to poke at it by hand.

---

## Edge cases handled

Each of these has at least one test.

**Auth** — duplicate email (case-insensitively); weak, mismatched or missing
passwords; malformed email or phone; wrong password, unknown email and
deactivated account all 401; a forged JWT; a blacklisted refresh token; a
patient trying to set `is_staff` on themselves.

**Booking** — appointment in the past, inside the 30-minute lead window, beyond
the 90-day horizon, or outside the centre's opening hours; an offering that is
unavailable or whose centre is deactivated; a centre that does not offer the
requested test; double-booking the same slot (rejected) while two *different*
patients booking it is fine; rebooking a slot that was cancelled; unparseable
datetimes and unknown status filters.

**Payment** — unknown booking reference (404); another patient's booking (404,
not 403 — the API never confirms it exists); paying a cancelled booking (409);
paying an already-confirmed booking (409); a second charge while one is in
flight (409); retrying after a decline (allowed); an `Idempotency-Key` reused
for a different booking (409); a payment stuck in PENDING (swept by
`reconcile_payments`).

**Webhook** — unsigned, wrongly signed, tampered-body and stale-timestamp
deliveries (401); non-JSON and non-object bodies, missing `id`/`type` (400);
unknown payment, unsupported event type, amount mismatch (200 + `IGNORED`);
the same event five times; two events asserting the same outcome; a
contradictory later event; a success landing after cancellation (payment
recorded, booking left cancelled, `refund_required` logged); a processing
failure (500, stored, replayable).

**Authorisation** — every booking and payment queryset is filtered by
`request.user` *before* lookup, so another user's reference is a 404. Catalogue
writes are staff-only. `?include_inactive=true` is rejected for non-staff
rather than silently ignored, so a cached response can never reach the wrong
audience.

---

## Project layout

```
config/            settings (+ settings_test), urls, celery, env parsing
apps/
  common/          error envelope, JSON logging, request-id middleware,
                   pagination, permissions, row-locking helper, health
  accounts/        email-based user, signup/login/refresh/logout/me
  catalog/         centres, tests, offerings; read caching + invalidation
  bookings/        Booking + audit trail; state machine in services.py
  payments/        Payment, WebhookEvent, simulated gateway, HMAC signing,
                   idempotent settlement, Celery tasks, ops commands
frontend/          single-page web app (index.html + static/app/{app.js,styles.css})
tests/             196 tests
docker/            entrypoint (wait for db → migrate → seed → run)
```

Views are thin: authenticate, deserialise, call a service. All business rules
live in `apps/*/services.py`, which is why the same logic can be driven by an
HTTP request, a Celery task or a management command without duplication.

### Management commands

| Command | Purpose |
| --- | --- |
| `seed_catalog` | 4 centres, 9 tests, 26 priced offerings, 2 demo users. Idempotent. |
| `send_test_webhook --payment PAY-… --times 3` | Deliver a signed webhook N times |
| `replay_failed_webhooks [--dry-run]` | Re-process failed/unhandled events |
| `reconcile_payments --older-than-minutes 30` | Fail payments stuck in PENDING |

### Bonus engineering included

Redis caching (version-keyed catalogue reads, invalidated by signals, `X-Cache`
header) · Celery worker with retry + backoff for webhook processing · Docker and
docker-compose with healthchecks and a non-root image · OpenAPI/Swagger ·
196 tests at 94% coverage · structured JSON logging with request-id correlation ·
pagination on every list · scoped rate limiting (login, payments, webhook) ·
webhook retry handling and a reconciliation sweeper · a health endpoint · Django
admin for back-office · GitHub Actions CI running the suite against Postgres.

---

## Tests

```bash
pytest                                  # 196 tests, in-memory SQLite
pytest --cov --cov-report=term-missing  # 94% coverage
DATABASE_URL=postgresql://… pytest      # same suite, against Postgres
```

| File | Covers |
| --- | --- |
| `test_auth.py` | Signup validation, login, refresh, logout/blacklist, profile |
| `test_catalog.py` | Filtering, per-centre pricing, staff-only writes, caching |
| `test_bookings.py` | Creation, price snapshot, time rules, isolation, cancellation |
| `test_payments.py` | Success/decline, retries, `Idempotency-Key`, reconciliation |
| `test_webhooks.py` | Signatures, malformed payloads, **idempotency**, races, retries |
| `test_state_machine.py` | All 16 transitions + every database constraint |
| `test_management.py` | Commands and Celery tasks |
| `test_ops.py` | Health, schema, request-id tracing, throttling, JSON logs |
| `test_frontend.py` | The app shell at `/`, its static assets, and that it doesn't shadow the API |

Tests use real JWTs rather than `force_authenticate`, so the token path is
exercised on every request.

---

## Configuration

Everything is environment-driven (see `.env.example`). Notable knobs:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DJANGO_DEBUG` | `true` | Local-dev default. Set `false` in any deployment: that switches on secure cookies, HSTS and JSON logs (`manage.py check --deploy` is then clean apart from the two opt-ins below) |
| `SECURE_SSL_REDIRECT` / `SECURE_HSTS_PRELOAD` | `false` | Opt-in. TLS is normally terminated and redirected at the load balancer; preload is hard to undo |
| `DATABASE_URL` | `postgresql://eve:eve@localhost:5432/eve_diagnostics` | `postgres://` or `sqlite://` |
| `REDIS_URL` | *(unset)* | Unset → local-memory cache, so it runs without Redis |
| `CELERY_BROKER_URL` | `REDIS_URL` | Unset → tasks run inline, still correct |
| `PAYMENT_DEFAULT_OUTCOME` | `random` | `success` / `failure` / `random` |
| `PAYMENT_SUCCESS_RATE` | `0.8` | Used when the outcome is random |
| `PAYMENT_WEBHOOK_SECRET` | `dev-webhook-secret` | HMAC key |
| `PAYMENT_WEBHOOK_REQUIRE_SIGNATURE` | `true` | Set false to test by hand |
| `BOOKING_MIN_LEAD_TIME_MINUTES` | `30` | Earliest bookable slot |
| `BOOKING_MAX_ADVANCE_DAYS` | `90` | Booking horizon |
| `BOOKING_CANCELLATION_CUTOFF_HOURS` | `2` | Cancellation window for confirmed bookings |
| `LOG_FORMAT` | `console` in debug, else `json` | Structured logging |

The service degrades gracefully: no Redis → local-memory cache; no broker →
inline task execution. Behaviour is identical, only the timing differs.

---

## Assumptions

1. **No slot capacity.** A centre is not modelled as having N slots per hour;
   any number of patients may book 10:00. Only *one patient* booking the *same
   offering* twice at the same time is prevented. Real capacity needs a slot
   table and is a bigger design than this brief calls for.
2. **The payment outcome is controllable.** `"outcome": "success" | "failure"`
   forces the result; otherwise `PAYMENT_SUCCESS_RATE` decides. Random-only
   outcomes would make the test suite non-deterministic and make a reviewer
   retry until the dice cooperate.
3. **Both settlement paths exist, and the webhook is authoritative.**
   `POST /payments/` settles synchronously because the brief says the simulated
   payment should update the booking. A real gateway also calls back, so the
   webhook re-asserts the outcome — and must be a no-op when it agrees. That
   overlap is the point of the exercise, not an accident.
4. **First terminal outcome wins.** A `payment.failed` arriving after a
   `payment.succeeded` is refused and logged, not applied. Flipping a confirmed
   booking on a late contradictory event is worse than ignoring it.
5. **404 rather than 403 for other users' resources**, so the API does not
   confirm that a booking reference exists.
6. **Money is `DECIMAL(10,2)` in a single currency (INR).** No floats. No
   multi-currency, no FX.
7. **Refunds are out of scope.** A charge that succeeds against a cancelled
   booking is recorded, the booking stays cancelled, and the case is logged with
   `requires_refund: true` for an operator. Silently confirming a cancelled
   appointment would be worse.
8. **Users are deactivated, never deleted** — `PROTECT` on the booking FK keeps
   financial history intact.
9. **Back-office access is the Django admin**, not user-facing API endpoints, so
   the booking/payment APIs can stay strictly caller-scoped.
10. **Report delivery, notifications, phlebotomist scheduling and home
    collection are out of scope.**

---

## What I would do with more time

**Correctness and scale**
- **Slot capacity and availability**, with a `Slot` table and `SELECT … FOR
  UPDATE SKIP LOCKED` to hand out concurrent bookings safely.
- **Outbox pattern** for anything we send outward, so a webhook we *emit* has
  the same delivery guarantees as the ones we receive.
- **Concurrency tests** driving real parallel requests against Postgres; the
  current suite proves the constraints exist and the locking path runs, not that
  N simultaneous callers behave (SQLite cannot demonstrate row locks).
- **Provider reconciliation**: `reconcile_payments` currently fails a stuck
  payment after a timeout. It should first re-query the provider and only give
  up if the provider also has no record.
- **Refund flow** for the cancelled-but-charged case, instead of only logging it.

**Operability**
- Sentry, plus Prometheus metrics for payment success rate, webhook duplicate
  rate and settlement latency — the three numbers that tell you this service is
  sick before a customer does.
- OpenTelemetry tracing across web → worker (the request-id groundwork is there).
- Celery Beat schedules for `reconcile_payments` and `replay_failed_webhooks`
  rather than running them by hand.

**Product and API**
- Postgres full-text search or trigram indexes for the catalogue; `icontains`
  will not survive a large test list.
- Cursor pagination for bookings, which page-number pagination handles badly
  when rows are inserted while a client is paging.
- Idempotency keys on booking creation too, not just payments.
- A soft "reservation hold" so a slot is not lost while payment is in flight.

**Frontend**
- Move tokens from `localStorage` to httpOnly, SameSite cookies with CSRF
  protection, so an XSS bug could not read a session.
- Browser tests (Playwright) for the book-and-pay flow, and a real bundler
  with TypeScript once the client grows past a demo.

**Engineering hygiene**
- Extend CI beyond the current workflow (`.github/workflows/ci.yml` already runs
  the suite against Postgres, checks for migration drift, builds the schema and
  builds the image) with `ruff`, a coverage gate and a published image.
- `mypy --strict` on the services layer.
- Split `settings.py` into `base/dev/prod` once there is a real deployment
  target; a single env-driven module is honest at this size.
