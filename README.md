# EVE Healthcare: Diagnostics Booking API

A backend service for booking diagnostic tests at partner centres and paying
for them through a **simulated payment provider** with an **idempotent
webhook**.

**Stack:** Python 3.12 · Django 4.2 · Django REST Framework · PostgreSQL ·
JWT · Redis · Celery · Docker. Ships with a small web frontend.

**Quality:** 226 tests at 95% coverage, CI on PostgreSQL, OpenAPI docs.

---

## The brief, and where each part lives

| Requirement | Implementation | Proof |
| --- | --- | --- |
| **1. Authentication**: signup, login, JWT, validation | `apps/accounts`: email login, JWT access + rotating refresh tokens, logout blacklists | `tests/test_auth.py` |
| **2. Centres & tests**: name, location, tests, price | `apps/catalog`: price lives on the centre↔test pair, so each centre sets its own | `tests/test_catalog.py` |
| **3. Bookings**: patient, test, centre, time, amount, status | `apps/bookings`: amount snapshotted, explicit `PENDING / CONFIRMED / FAILED / CANCELLED` state machine | `tests/test_bookings.py`, `tests/test_state_machine.py` |
| **4. `POST /payments/`**: SUCCESS or FAILED, updates the booking | `apps/payments`: simulated gateway behind a swappable interface | `tests/test_payments.py` |
| **5. `POST /payments/webhook/`**: idempotent | Signed deliveries, unique event IDs, row locks, first outcome wins | `tests/test_webhooks.py`, `tests/test_concurrency.py` |
| **6. Edge cases** | Invalid input, replays, bad IDs, failed payments, cross-user access | [Edge cases](#6-edge-cases) |
| **Bonus**: all nine | Redis cache · Celery · Docker · Swagger · tests · JSON logs · pagination · rate limits · webhook retries | [Bonus engineering](#bonus-engineering) |

## Contents

1. [Run it locally](#1-run-it-locally)
2. [Architecture](#2-architecture)
3. [API reference and example requests](#3-api-reference-and-example-requests)
4. [Database design](#4-database-design)
5. [Idempotency and reliability](#5-idempotency-and-reliability)
6. [Edge cases](#6-edge-cases)
7. [Testing](#7-testing)
8. [Configuration](#8-configuration)
9. [Assumptions](#9-assumptions)
10. [What I would improve with more time](#10-what-i-would-improve-with-more-time)
11. [Web app screenshots](#web-app-screenshots)

---

## 1. Run it locally

### Option A: Docker (one command, recommended)

```bash
docker compose up --build
```

This starts PostgreSQL, Redis, the API and a Celery worker. Migrations run
and demo data is seeded automatically.

| Open | URL |
| --- | --- |
| Web app | <http://localhost:8000/> |
| API docs (Swagger) | <http://localhost:8000/api/docs/> |
| API root | <http://localhost:8000/api/v1/> |
| Admin | <http://localhost:8000/admin/> |
| Health check | <http://localhost:8000/health/> |

| Demo account | Email | Password |
| --- | --- | --- |
| Patient | `patient@example.com` | `DemoPass123!` |
| Staff / admin | `admin@example.com` | `AdminPass123!` |

If a port is already in use on your machine, override it:

```bash
WEB_PORT=8001 DB_PORT=5433 REDIS_PORT=6380 docker compose up --build
```

### Option B: Without Docker

Requires Python 3.11+. PostgreSQL is the production target; SQLite works for
a quick look.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export DATABASE_URL=sqlite:///dev.sqlite3               # or postgresql://user:pass@localhost:5432/eve
python manage.py migrate
python manage.py seed_catalog
python manage.py runserver
```

Without Redis the app uses an in-memory cache, and without a broker it
processes webhooks inline. Behaviour is identical; only the timing differs.

### Run the tests

```bash
pytest                                                   # in-memory SQLite, no setup
DATABASE_URL=postgresql://eve:eve@localhost:5432/eve pytest   # full suite on PostgreSQL
```

---

## 2. Architecture

### System overview

![System architecture: the web app, API clients and a simulated payment provider call one Django API in docker compose, which uses PostgreSQL, Redis and a Celery worker](docs/architecture.svg)

| Component | Role |
| --- | --- |
| **web** | One Django process serving the REST API, the OpenAPI docs, the admin and the web app. Stateless; scale it horizontally. |
| **worker** | Celery worker that processes webhook deliveries off the request path, with retries and backoff. |
| **PostgreSQL** | The source of truth. Correctness rules (no double booking, no double charge, no duplicate webhook) are enforced here with unique and check constraints, not only in Python. |
| **Redis** | Two jobs: caching catalogue reads, and carrying Celery tasks. Losing it degrades speed, never correctness. |
| **Payment provider** | Simulated (the brief forbids a real gateway). It answers charges synchronously and calls the webhook, signed with a shared HMAC secret. |

### Code structure: thin views, rules in services

Every request passes through the four layers numbered in the diagram, and
the business rules live in exactly one of them:

1. **Middleware** gives each request an ID and writes one structured log line.
2. **Views** (Django REST Framework) check the JWT, permissions and rate limit,
   validate input, then hand off. They contain no business rules.
3. **Services** (`apps/*/services.py`) hold every rule: the booking state
   machine, idempotent payment settlement, webhook processing. Celery tasks
   and management commands call the same services, so there is one
   implementation of each rule.
4. **Models** map to PostgreSQL, where constraints and row locks back up the
   rules.

Any exception, at any layer, comes back as one JSON error shape with a stable
code (see [Errors](#errors)).

```
config/            settings (+ test settings), URLs, Celery app, env parsing
apps/
  common/          error envelope, JSON logging, request-ID middleware,
                   pagination, permissions, row-locking helper, health + config
  accounts/        user model (email login), signup / login / refresh / logout / me
  catalog/         centres, tests, priced offerings; cached reads
  bookings/        Booking + audit trail; the state machine lives in services.py
  payments/        Payment, WebhookEvent, simulated gateway, HMAC signing,
                   idempotent settlement, Celery tasks, ops commands
frontend/          the web app (index.html + static/app/app.js, styles.css)
tests/             226 tests
docker/            container entrypoint: wait for DB → migrate → seed → run
```

### Workflow 1: booking and paying

![Booking and payment flow: compare prices, book a slot with the price saved, pay with the booking row locked; approved confirms the booking, declined fails it and allows a retry](docs/booking-payment-flow.svg)

<details>
<summary>Step-by-step sequence (what the API and database do)</summary>

```mermaid
sequenceDiagram
    autonumber
    actor P as Patient
    participant API as Django API
    participant DB as PostgreSQL
    participant GW as Payment gateway

    P->>API: POST /bookings/ {offering, scheduled_for}
    API->>DB: validate slot, snapshot price, INSERT booking (PENDING)
    API-->>P: 201 {reference, status: PENDING, amount}

    P->>API: POST /payments/ {booking} + Idempotency-Key
    API->>DB: SELECT booking FOR UPDATE (serialises parallel attempts)
    API->>DB: re-check the key, refuse if paid / cancelled / in progress
    API->>DB: INSERT payment (PENDING)
    API->>GW: charge(amount)
    GW-->>API: SUCCESS or FAILED
    API->>DB: lock payment, settle it, move booking to CONFIRMED / FAILED, write audit row
    API-->>P: 201 {payment status, booking status}
```

</details>

### Workflow 2: the idempotent webhook

![Idempotent webhook flow: verify the signature, store the event under a unique ID, apply a new event once, count and ignore a duplicate](docs/webhook-flow.svg)

<details>
<summary>Step-by-step sequence (including the Celery worker)</summary>

```mermaid
sequenceDiagram
    autonumber
    participant PR as Payment provider
    participant API as Django API
    participant DB as PostgreSQL
    participant Q as Redis queue
    participant W as Celery worker

    PR->>API: POST /payments/webhook/ (signed, event id evt_123)
    API->>API: verify HMAC signature + timestamp
    API->>DB: INSERT webhook_event (event_id is UNIQUE)
    alt new event
        API->>Q: enqueue processing
        API-->>PR: 200 {duplicate: false}
        Q->>W: process evt_123
        W->>DB: lock payment row, apply outcome (first outcome wins)
        W->>DB: move booking through the state machine, mark event PROCESSED
    else same event delivered again
        DB-->>API: unique-constraint violation
        API->>DB: delivery_count + 1 (nothing else changes)
        API-->>PR: 200 {duplicate: true}
    end
    Note over W,DB: On failure the event is marked FAILED and<br/>retried with exponential backoff
```

</details>

### Key design decisions

1. **Rules live in the database, not only in code.** Checks like "is this
   already paid?" can lose a race between two requests; a unique index cannot.
   Every money-related invariant has a constraint behind it (see
   [Database design](#4-database-design)).
2. **One function settles payments.** The synchronous charge and the webhook
   both call `apply_payment_outcome()`, which locks the payment row and applies
   an outcome at most once. Two paths, one set of rules.
3. **One function changes booking status.** `transition_booking()` enforces
   the allowed transitions and writes an audit row. No view ever assigns
   `booking.status` directly.
4. **Store first, process second.** Each webhook is saved before it's acted
   on, so a crash can't lose it. The retry task or `replay_failed_webhooks`
   picks it up.
5. **The gateway is behind an interface.** Swapping the simulator for Razorpay
   or Stripe means implementing `charge()` in one module; the services don't
   change.
6. **Degrade gracefully.** Without Redis, an in-memory cache is used; without
   a broker, tasks run inline. Same behaviour, different timing.

---

## 3. API reference and example requests

Interactive docs at **`/api/docs/`** (Swagger) and `/api/redoc/`, schema at
`/api/schema/`. Everything is under `/api/v1/`. The two paths the brief names,
`POST /payments/` and `POST /payments/webhook/`, also work at the root.

### Endpoints

**Auth**

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/signup/` | none | Create an account |
| POST | `/api/v1/auth/login/` | none | Email + password → access + refresh tokens |
| POST | `/api/v1/auth/refresh/` | none | New token pair (refresh tokens rotate) |
| POST | `/api/v1/auth/logout/` | JWT | Blacklist the refresh token |
| GET / PATCH | `/api/v1/auth/me/` | JWT | Read or update your profile |

**Catalogue** (read: any signed-in user · write: staff only)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/centres/` | Centres. Filters: `?city=` `?search=` `?test=` |
| GET | `/api/v1/centres/{id}/` | One centre with its price list |
| GET | `/api/v1/centres/{id}/tests/` | Tests offered by a centre, with prices |
| GET | `/api/v1/tests/` | Test catalogue. Filters: `?search=` `?category=` |
| GET | `/api/v1/offerings/` | A test at a centre, with its price. Filters: `?test=` `?city=` `?max_price=` `?ordering=price` |
| POST / PATCH / DELETE | any of the above | Staff only; patients get 403 |

**Bookings** (always limited to the caller's own)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/bookings/` | Your bookings. Filters: `?status=` `?from=` `?to=` `?ordering=` |
| POST | `/api/v1/bookings/` | Book a test |
| GET | `/api/v1/bookings/{reference}/` | One booking with its full status history |
| POST | `/api/v1/bookings/{reference}/cancel/` | Cancel it |

**Payments**

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/v1/payments/` | JWT | Pay for a booking (simulated). Honours an `Idempotency-Key` header |
| GET | `/api/v1/payments/` | JWT | Your payments. Filters: `?status=` `?booking=` |
| GET | `/api/v1/payments/{reference}/` | JWT | One payment |
| POST | `/api/v1/payments/webhook/` | HMAC signature | Payment-status callback from the provider. **Idempotent** |

**Ops:** `GET /health/` (database + cache check) · `GET /api/v1/config/`
(non-secret settings for the web app).

All lists are paginated: `{count, page, pages, page_size, next, previous,
results}`, with `?page=` and `?page_size=` (max 100).

### Example requests

```bash
BASE=http://localhost:8000

# 1. Log in as the demo patient
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"patient@example.com","password":"DemoPass123!"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access"])')

# 2. Where can I get a complete blood count, cheapest first?
curl -s "$BASE/api/v1/offerings/?test=CBC&ordering=price" -H "Authorization: Bearer $TOKEN"

# 3. Book it (use an offering id from step 2)
curl -s -X POST $BASE/api/v1/bookings/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"offering": 1, "scheduled_for": "2026-10-15T10:00:00+05:30"}'
# → 201 {"reference": "BKG-…", "status": "PENDING", "amount": "350.00", …}

# 4. Pay. The Idempotency-Key makes a retry safe: replaying it returns
#    the same payment (200) instead of charging again.
curl -s -X POST $BASE/payments/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-key-1' \
  -d '{"booking": "BKG-…", "method": "UPI"}'
# → 201 {"reference": "PAY-…", "status": "SUCCESS", "booking_status": "CONFIRMED"}
```

The gateway approves most payments and declines some, like a real one
(`PAYMENT_SUCCESS_RATE`, 80% by default). For predictable results, pass
`"outcome": "success" | "failure"`, or `"pending"` to leave the payment
waiting for the webhook, as an asynchronous UPI payment would.

**Watch the webhook ignore duplicates.** Deliver one signed event three times:

```bash
docker compose exec web python manage.py send_test_webhook --payment PAY-… --times 3
```

```
delivery 1/3: HTTP 200 {'duplicate': False, 'status': 'RECEIVED', 'result': 'queued'}
delivery 2/3: HTTP 200 {'duplicate': True,  'status': 'RECEIVED', 'result': 'already_recorded'}
delivery 3/3: HTTP 200 {'duplicate': True,  'status': 'RECEIVED', 'result': 'already_recorded'}
```

One event row is stored (`delivery_count = 3`) and processed exactly once by
the worker. Add `--print-only` to get a signed `curl` command instead.

### Errors

Every error has the same shape, with a stable machine-readable `code`:

```json
{
  "error": {
    "code": "invalid_state_transition",
    "message": "A booking that is CANCELLED cannot become CONFIRMED.",
    "details": { "from": "CANCELLED", "to": "CONFIRMED", "booking": "BKG-3F91A0C2D5" }
  },
  "request_id": "0f2c8d1a4b…"
}
```

| Status | Meaning |
| --- | --- |
| 400 | Invalid payload or query parameter |
| 401 | Missing or expired token, or a bad webhook signature |
| 403 | Signed in but not allowed (e.g. a patient editing the catalogue) |
| 404 | Not found. **Also** returned for another user's booking, so the API never confirms it exists |
| 409 | Valid request, but the current state forbids it (already paid, cancelled, duplicate) |
| 429 | Rate limited |

The `request_id` also comes back in the `X-Request-ID` header and appears on
every log line for that request, so one ID traces a request end to end.

---

## 4. Database design

```mermaid
erDiagram
    USER ||--o{ BOOKING : places
    DIAGNOSTIC_CENTRE ||--o{ CENTRE_TEST : offers
    DIAGNOSTIC_TEST ||--o{ CENTRE_TEST : "offered as"
    CENTRE_TEST ||--o{ BOOKING : "booked as"
    BOOKING ||--o{ PAYMENT : "paid by"
    BOOKING ||--o{ BOOKING_STATUS_CHANGE : "audited by"
    PAYMENT ||--o{ WEBHOOK_EVENT : "settled by"

    CENTRE_TEST {
        decimal price
        bool is_available
    }
    BOOKING {
        string reference UK
        datetime scheduled_for
        decimal amount "snapshot of price"
        string status
    }
    PAYMENT {
        string reference UK
        string status
        string idempotency_key UK
        string provider_reference UK
    }
    WEBHOOK_EVENT {
        string event_id UK
        string status
        int delivery_count
    }
```

| Table | Holds |
| --- | --- |
| `users` | Email-based accounts. Deactivated, never deleted, so financial history stays intact. |
| `diagnostic_centres` | Name, address, city, pincode, opening hours, active flag. |
| `diagnostic_tests` | Master catalogue: code, name, category, preparation, report time. |
| `centre_tests` | **A test at a centre, with that centre's price.** Unique per `(centre, test)`. |
| `bookings` | Patient, offering, appointment time, **amount snapshot**, status. |
| `booking_status_changes` | Append-only audit trail: every transition and what caused it. |
| `payments` | Amount, status, method, provider reference, idempotency key, raw provider payload. |
| `webhook_events` | Every webhook received, saved before processing; `delivery_count` counts redeliveries. |

`webhook_events` finds its payment through the provider's reference rather
than a foreign key, because a provider can send an event about a payment we
have never seen. That event must still be stored (and marked `IGNORED`), not
rejected by the database.

### Design decisions

**Price belongs to the centre↔test pair.** A CBC costs ₹299 at one centre and
₹420 at another, so a single `price` column on the test would be wrong as
soon as a second centre exists. A booking points at the offering, which gives
it the centre and the test through one foreign key.

**A booking's amount is a snapshot.** It's copied from the price at booking
time. Repricing a test later must never change what a patient already agreed
to pay, and a test checks exactly that.

**Public IDs are opaque.** Bookings and payments are exposed as `BKG-…` and
`PAY-…` references, never sequential integers, so records can't be
enumerated by guessing IDs.

**Invariants are database constraints:**

| Constraint | Prevents |
| --- | --- |
| Unique `(centre, test)` | Two prices for one test at one centre |
| Partial unique `(user, offering, time)` while `PENDING` or `CONFIRMED` | Double-booking a slot, while still allowing a cancelled slot to be rebooked |
| Partial unique `(booking)` while payment is `PENDING` or `SUCCESS` | Charging a booking twice, while still allowing a retry after a decline |
| Unique `idempotency_key` | A retried `POST /payments/` creating a second charge |
| Unique `webhook_events.event_id` | Processing a redelivered webhook twice |
| Check `amount > 0` / `amount ≥ 0` | Nonsensical money |
| `PROTECT` on booking → user / offering | Deleting rows that financial records depend on |

Indexes match the real queries: `(user, status)` and `(status,
scheduled_for)` on bookings, `(test, price)` on offerings, `(booking,
status)` on payments. Money is `DECIMAL(10,2)`, never a float.

### Booking state machine

```mermaid
stateDiagram-v2
    [*] --> PENDING: booked
    PENDING --> CONFIRMED: payment succeeded
    PENDING --> FAILED: payment declined
    PENDING --> CANCELLED: patient cancels
    FAILED --> PENDING: new payment attempt
    FAILED --> CANCELLED: patient cancels
    CONFIRMED --> CANCELLED: cancelled before the cutoff
    CANCELLED --> [*]
```

The allowed transitions are defined once, in
`apps/bookings/services.py::ALLOWED_TRANSITIONS`. A move to the status a
booking already has is a no-op rather than an error, which is what lets a
repeated webhook pass through safely. All 16 from→to pairs are tested.

---

## 5. Idempotency and reliability

*Idempotent* means doing the same thing twice has the same effect as doing
it once. Payment providers routinely resend webhooks, and clients retry
requests on timeouts, so this is the core of the brief.

**How the webhook stays idempotent**

| Mechanism | What it guarantees |
| --- | --- |
| **Unique `event_id`** | A redelivered event fails the insert and is answered `200 {duplicate: true}`. The database arbitrates, so two simultaneous deliveries can't both slip through, which a "check then insert" in Python would allow. |
| **Row lock on the payment** (`SELECT … FOR UPDATE`) | Two events about the same payment are applied one after the other, never interleaved. |
| **First outcome wins** | Repeating the current outcome is a no-op. A contradicting later event (`failed` after `succeeded`) is refused and logged; a paid booking never flips. |
| **Signed deliveries** | `X-Webhook-Signature: t=<unix>,v1=<HMAC-SHA256>`, compared in constant time and rejected outside a 5-minute window. An unsigned endpoint that confirms bookings would be an open door. |
| **Right status codes** | Unknown payment, unsupported type or amount mismatch: stored, marked `IGNORED`, answered 200, since retrying can never succeed. Real processing errors: 500, so the provider retries. |

**How payments stay idempotent:** a client sends an `Idempotency-Key` header.
Replaying a key returns the original payment instead of charging again. The
key is checked again *after* the booking row is locked, so parallel retries
with the same key all get the same payment back.

**Retries:** webhook processing runs on Celery with exponential backoff (10s,
20s, 40s…, up to 5 attempts). Recording the failure is best-effort, so a
database outage, the most common transient failure, can't cancel the retry.
`replay_failed_webhooks` and `reconcile_payments` cover anything left behind.

**Verified under real conditions**, not only in unit tests:

| Scenario (8 parallel requests on PostgreSQL) | Result |
| --- | --- |
| One webhook event delivered 8× at once | Processed once, 7 duplicates, `delivery_count = 8` |
| 8 different events with the same outcome | Booking moves once |
| `succeeded` and `failed` events racing | Exactly one wins; booking always matches the payment |
| 8 simultaneous charges for one booking | 1 succeeds, 7 rejected, one payment row |
| 8 retries with one `Idempotency-Key` | One payment; all 8 callers get it back |
| 8 simultaneous bookings of one slot | 1 succeeds, 7 rejected |

The full Docker stack was also run with a **deliberate database outage**
while a webhook was being processed. The attempt failed, a retry was
scheduled, and it confirmed the booking seconds after the database came back.

Testing like this found and fixed real bugs, each now covered by a test: an
`Idempotency-Key` that wasn't re-checked under the lock, a retry cancelled by
the outage it was meant to survive, a missing container restart policy, and a
refresh-token rotation race that logged users out on reload. That one is
fixed with a 30-second reuse window (`JWT_REFRESH_REUSE_SECONDS`).

---

## 6. Edge cases

Every item below has at least one test.

- **Auth:** duplicate email (case-insensitive) · weak, mismatched or missing
  passwords · malformed email or phone · wrong password, unknown email,
  deactivated account → 401 · forged JWT · blacklisted refresh token · a user
  trying to make themselves staff · all invalid signup fields reported in one
  response.
- **Bookings:** time in the past, inside the 30-minute lead window, beyond 90
  days, or outside opening hours · unavailable offering or deactivated centre ·
  a centre that doesn't offer the test · double-booking one slot (rejected),
  while two *different* patients may book it · rebooking a cancelled slot ·
  unparseable dates and unknown filters.
- **Payments:** unknown booking (404) · another user's booking (404, not 403) ·
  paying a cancelled booking (409) · paying twice (409) · a second charge while
  one is in progress (409) · retry after a decline (allowed) · an
  `Idempotency-Key` reused for another booking (409) · payments stuck pending
  (swept by `reconcile_payments`).
- **Webhooks:** unsigned, wrongly signed, tampered or stale (401) · non-JSON
  body or missing `id` / `type` (400) · unknown payment, unsupported type,
  amount mismatch (200, ignored) · the same event 5× · a contradicting late
  event · a success arriving after cancellation (recorded, booking stays
  cancelled, refund flagged) · processing failure (500, stored, retried).
- **Authorisation:** every booking and payment query is filtered by the
  signed-in user *before* lookup · catalogue writes are staff-only.

---

## 7. Testing

```bash
pytest                                     # SQLite: 220 run, 6 PostgreSQL-only tests skip
pytest --cov --cov-report=term-missing     # 95% coverage
DATABASE_URL=postgresql://… pytest         # all 226, including the concurrency tests
```

| File | Covers |
| --- | --- |
| `test_auth.py` | Signup validation, login, token refresh and reuse window, logout, profile |
| `test_catalog.py` | Filters, per-centre pricing, staff-only writes, caching |
| `test_bookings.py` | Creation, price snapshot, time rules, isolation, cancellation |
| `test_payments.py` | Success and decline, retries, `Idempotency-Key`, pending payments, rate-limit scope |
| `test_webhooks.py` | Signatures, malformed payloads, **duplicates**, races, retries |
| `test_concurrency.py` | 8 truly parallel requests per scenario (PostgreSQL only) |
| `test_state_machine.py` | All 16 transitions and every database constraint |
| `test_management.py` | Management commands and Celery tasks, including a DB outage |
| `test_ops.py` | Health, OpenAPI schema, request IDs, throttling, JSON logs, config |
| `test_frontend.py` | The web app shell, its assets, cache-safe asset URLs |
| `test_config.py` | `DATABASE_URL` parsing and environment helpers |

Tests authenticate with real JWTs rather than bypassing auth, so the token
path runs on every request. **CI** (`.github/workflows/ci.yml`) runs the
whole suite on PostgreSQL, checks for missing migrations, validates the
OpenAPI schema and builds the Docker image on every push.

---

## 8. Configuration

Everything is set through environment variables; see `.env.example`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | local PostgreSQL | `postgresql://…` or `sqlite://…` |
| `REDIS_URL` | unset | Unset → in-memory cache |
| `CELERY_BROKER_URL` | `REDIS_URL` | Unset → tasks run inline |
| `DJANGO_DEBUG` | `true` | Set `false` in any deployment: enables secure cookies, HSTS, JSON logs |
| `DJANGO_SECRET_KEY` | dev value | Also the JWT signing key; use ≥ 32 random bytes |
| `PAYMENT_SUCCESS_RATE` | `0.8` | Share of payments the simulated gateway approves |
| `PAYMENT_DEFAULT_OUTCOME` | `random` | `success`, `failure`, `pending` or `random` |
| `PAYMENT_WEBHOOK_SECRET` | dev value | Shared HMAC secret for webhook signatures |
| `PAYMENT_WEBHOOK_MAX_RETRIES` | `5` | Webhook retry attempts |
| `BOOKING_MIN_LEAD_TIME_MINUTES` | `30` | Earliest bookable slot |
| `BOOKING_MAX_ADVANCE_DAYS` | `90` | Booking horizon |
| `BOOKING_CANCELLATION_CUTOFF_HOURS` | `2` | Confirmed bookings can't be cancelled later than this |
| `THROTTLE_*` | see `settings.py` | Rate limits: anon 60/min, user 240/min, login 10/min, payments 20/min |

### Bonus engineering

| Bonus item | How |
| --- | --- |
| Redis caching | Catalogue reads cached with a version number that every catalogue change bumps; `X-Cache: HIT/MISS` header |
| Celery / background jobs | Webhook processing, failed-webhook replay, stuck-payment reconciliation |
| Docker & compose | Non-root image; `db`, `redis`, `web`, `worker` with health checks and restart policies |
| Swagger / OpenAPI | `/api/docs/`, `/api/redoc/`, `/api/schema/`, 35 documented operations |
| Tests | 226 tests, 95% coverage, CI on PostgreSQL |
| Structured logging | JSON lines with a request ID on every record |
| Pagination | On every list, with a client-controlled, capped page size |
| Rate limiting | Separate limits for anonymous users, signed-in users, login, payments and the webhook |
| Webhook retries | Exponential backoff, a replay command, verified against a real DB outage |

**Management commands:** `seed_catalog` (demo data, safe to re-run) ·
`send_test_webhook` · `replay_failed_webhooks` · `reconcile_payments`.

**Web app:** a light-theme single-page app served by Django at `/`, so the
whole product runs from one container. Browse centres, compare prices,
book a slot, pay, and follow each booking's lifecycle. It's plain JavaScript
with no build step, talks to the same documented API, and works on phones.

---

## 9. Assumptions

1. **No slot capacity.** Any number of patients can book the same 10:00 slot;
   only the *same* patient booking the same test twice at once is prevented.
   Real capacity needs a slots table, a bigger design than the brief calls for.
2. **Both settlement paths exist, and they agree.** `POST /payments/` settles
   immediately, as the brief asks. The provider's webhook then confirms the
   same outcome, and must be a no-op when it agrees. That overlap is
   deliberate: it's exactly where idempotency matters.
3. **The first final outcome wins.** A late `failed` after `succeeded` is
   refused and logged. Flipping a paid booking is worse than ignoring a stale
   message.
4. **Payment outcomes are controllable through the API only.** Tests need
   predictable results; patients don't choose whether a payment succeeds, so
   the web app never sends `outcome`.
5. **404, not 403, for other users' records**, so the API never confirms that
   someone else's booking exists.
6. **One currency (INR), `DECIMAL(10,2)`.** No floats, no foreign exchange.
7. **Refunds are out of scope.** A payment that succeeds after the booking was
   cancelled is recorded, the booking stays cancelled, and a refund is flagged
   in the logs for an operator.
8. **Users are deactivated, never deleted**, to keep financial history.
9. **Back-office work uses the Django admin**, so the patient APIs stay
   strictly limited to the caller's own data.
10. **Out of scope:** reports, notifications, home sample collection.

---

## 10. What I would improve with more time

**Correctness and scale**
- Real slot capacity, with a slots table and `SELECT … FOR UPDATE SKIP LOCKED`.
- A refund flow for payments that succeed after cancellation.
- Reconciliation that asks the provider before failing a stuck payment.
- An outbox table, so outgoing events get the same guarantees as incoming ones.
- Load testing (k6 or Locust): the concurrency tests prove correctness, not
  throughput.

**Operations**
- Sentry, plus metrics for payment success rate, webhook duplicate rate and
  settlement latency.
- Celery Beat to run reconciliation and replay on a schedule.
- Tracing across web and worker (request IDs already flow through).

**API and product**
- A real payment gateway (Razorpay), behind the existing `charge()` interface.
- Full-text search for the catalogue, and cursor pagination for bookings.
- Idempotency keys on booking creation as well as payments.

**Code quality**
- `ruff`, a coverage gate and `mypy --strict` on the services layer in CI.
- Split settings into `base / dev / prod` once there is a real deployment.
- Move web-app tokens from `localStorage` to httpOnly cookies, and add
  browser tests (Playwright) for the booking flow.

---

## Web app screenshots

The web app is served by the same Django server at `/`. Run the project, open
<http://localhost:8000/> and log in as the demo patient. The backend is the
focus; the web app is a thin client over the same documented API.

<table>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/login.png" alt="Log in: JWT auth, with a one-click fill for the seeded demo patient"><br><sub><b>Log in:</b> JWT auth, with a one-click fill for the seeded demo patient</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/explore.png" alt="Explore: test search, popular tests and partner centres"><br><sub><b>Explore:</b> test search, popular tests and partner centres</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/tests.png" alt="Test catalogue: filter by category, with report times"><br><sub><b>Test catalogue:</b> filter by category, with report times</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/test-detail.png" alt="Compare prices: the same test at every centre, cheapest first"><br><sub><b>Compare prices:</b> the same test at every centre, cheapest first</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/centre.png" alt="Centre page: opening hours and that centre's own price list"><br><sub><b>Centre page:</b> opening hours and that centre's own price list</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/booking.png" alt="Book a slot: only times inside the centre's opening hours"><br><sub><b>Book a slot:</b> only times inside the centre's opening hours</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/pay.png" alt="Pay: the booking is PENDING with its price saved until payment"><br><sub><b>Pay:</b> the booking is PENDING with its price saved until payment</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/payment-result.png" alt="Payment received: the booking moves to CONFIRMED"><br><sub><b>Payment received:</b> the booking moves to CONFIRMED</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/my-bookings.png" alt="My bookings: grouped by upcoming and past, filter by status"><br><sub><b>My bookings:</b> grouped by upcoming and past, filter by status</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/booking-detail.png" alt="Booking detail: the state machine, with this booking's path in bold"><br><sub><b>Booking detail:</b> the state machine, with this booking's path in bold</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/payments.png" alt="Payments: every attempt, including declines with the reason"><br><sub><b>Payments:</b> every attempt, including declines with the reason</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/duplicate-booking.png" alt="Edge case: booking the same test at the same time is refused"><br><sub><b>Edge case:</b> booking the same test at the same time is refused</sub></td>
  </tr>
</table>

**On a phone** (below 900px the top menu becomes a bottom tab bar), and the
**Swagger UI** at `/api/docs/`, where every endpoint can be tried:

<table>
  <tr>
    <td width="32%" valign="top"><img src="docs/screenshots/mobile.png" alt="My bookings on a phone, with the bottom tab bar"></td>
    <td width="68%" valign="top"><img src="docs/screenshots/swagger.png" alt="Swagger UI listing the API endpoints"></td>
  </tr>
</table>
