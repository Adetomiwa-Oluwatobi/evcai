# EV Climate Intelligence Platform

A telemetry-driven MRV (Measurement, Reporting & Verification) backend that
converts electric vehicle activity into auditable, high-integrity climate
impact records. Built for the GEE774 pilot (10-50 electric tricycles/motorcycles
across Nigeria's LGAs), but hardware-agnostic — any device that can POST JSON
over HTTPS can report to it.

## Architecture

```
Vehicle dongle (real, or simulator.py for now)
        |
        v
POST /telemetry  --------->  Device auth (auth.py)
                                    |
                                    v
                        Anomaly detection (anomaly.py)
                        - GPS jump detection
                        - Energy balance check
                        - Confidence score (0-100)
                                    |
                                    v
                        Emissions calculation (emissions.py)
                        - E_base = Distance x EF_ICE
                        - E_project = kWh_consumed x EF_grid
                        - E_avoided = E_base - E_project
                                    |
                                    v
                        Audit hashing (audit.py)
                        - Tamper-evident hash chain
                                    |
                                    v
                        PostgreSQL (database.py)
                                    |
                    +---------------+---------------+
                    v                               v
        GET /fleet/status              GET /reports/verification
        (Fleet Operator view)          (Climate Verifier report)
```

## Setup

### 1. Prerequisites
- Python 3.11+
- PostgreSQL 14+ running locally (or update `DATABASE_URL` to point elsewhere)

### 2. Install dependencies
```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env: set a real ADMIN_API_KEY (generate one with the command in the comment)
# Update DATABASE_URL if your Postgres setup differs from the default
```

### 4. Create the database
```bash
createdb telemetry_db
```

### 5. Run migrations
```bash
alembic upgrade head
```

### 6. Start the server
```bash
uvicorn main:app --reload
```

API docs (interactive Swagger UI) will be available at `http://localhost:8000/docs`.

## Generating test data (no hardware needed yet)

Since hardware selection is still pending, use the simulator to generate
realistic fleet telemetry — including occasional injected anomalies, so
you can watch the detection layer work:

```bash
# Quick smoke test
python simulator.py --vehicles 1 --readings 10 --fast

# Pilot-scale load test
python simulator.py --vehicles 50 --readings 100 --anomaly-rate 0.05

# Real-time pacing (mimics real hardware sending every 10s)
python simulator.py --vehicles 5 --readings 30 --interval 10
```

The simulator caches generated device API keys locally in
`simulator_devices.json` (gitignored) so repeated runs reuse the same
simulated vehicles instead of re-registering every time.

## Running tests

```bash
pytest tests/ -v
```

Tests are split into:
- **Pure logic tests** (`test_emissions.py`, `test_anomaly.py`, `test_audit.py`) — no database needed, run instantly
- **Integration tests** (`test_api.py`) — exercise the full HTTP API against an isolated `telemetry_test_db` (create it once with `createdb telemetry_test_db`)

## API overview

| Endpoint | Purpose | Auth |
|---|---|---|
| `POST /devices/register?vin=...` | Register a new vehicle, get its API key (shown once) | Admin key |
| `POST /telemetry` | Submit a telemetry reading | Device key |
| `GET /fleet/status` | Live fleet state (Fleet Operator view) | None (add auth before production) |
| `GET /reports/verification?vin=...` | Aggregated climate impact report (Climate Verifier view) | Admin key |
| `GET /telemetry/{vin}/summary` | Per-vehicle running totals | None |
| `GET /vehicles/{vin}/detail` | Full vehicle detail: name, current state, totals, recent readings (used by the dashboard's click-through modal) | None |
| `GET /admin/simulate/presets` | List the 3 fixed simulation profiles | Admin key |
| `POST /admin/simulate?profile=...` | Start a background simulation run (quick/pilot/realtime) | Admin key |
| `GET /admin/simulate/{run_id}` | Poll a run's live status and log | Admin key |
| `GET /audit/verify` | Verify the entire hash chain hasn't been tampered with | None |

## Schema changes (Alembic)

Whenever you modify a model in `database.py`:

```bash
alembic revision --autogenerate -m "describe your change"
# Review the generated file in alembic/versions/ before applying!
alembic upgrade head
```

## Dashboard

A live operations console is served at `/dashboard` (and `/` redirects there).
It shows fleet-wide status (auto-refreshing every 10s), a click-through detail
view per vehicle (name, current state, running totals, recent reading
history), and an admin-key-gated verifier report generator.

## Dashboard

A live operations console is served at `/dashboard` (and `/` redirects there).
It shows fleet-wide status (auto-refreshing every 10s), a click-through detail
view per vehicle (name, current state, running totals, recent reading
history), an admin-key-gated verifier report generator, and a **Simulation
Control panel** — three one-click presets (quick smoke test, pilot-scale load
test, real-time demo) that generate realistic fleet data without touching a
terminal. Useful while hardware is still pending, and for demos.

Set `ENABLE_SIMULATOR_ROUTES=false` in your environment to disable the
simulation endpoints once real hardware is onboarded — they shouldn't exist
in a genuine production deployment.

## Deployment

`Dockerfile` and `start.sh` are included for deploying to any container host
(Render, Fly.io, Railway, etc.). `start.sh` runs `alembic upgrade head`
automatically before starting the server, so migrations are never missed on
deploy.

Minimum required environment variables in production:
- `DATABASE_URL` — point this at a real Postgres instance (e.g. Neon, Render Postgres, RDS)
- `ADMIN_API_KEY` — generate a fresh one, do not reuse your local dev key

```bash
docker build -t ev-climate-platform .
docker run -p 8000:8000 -e DATABASE_URL=... -e ADMIN_API_KEY=... ev-climate-platform
```

## Known gaps / things to resolve before production

- **Hardware not yet selected.** `EF_ICE` and `EF_grid` in `emissions.py` are
  placeholder values — replace with real figures from an ICE baseline survey
  and regional grid research before any output is used for actual carbon
  reporting.
- **`/fleet/status` has no auth** — add before deploying beyond local dev.
- **API keys are hashed with SHA-256** — fine for random 256-bit keys, but if
  this evolves into human-password auth later, switch to a proper password
  hasher (e.g. argon2/bcrypt).
- **HTTPS is not configured** — API keys must never travel over plain HTTP in
  production.
- **No CI pipeline yet** — tests exist but aren't run automatically on push.

## Project structure

```
main.py           - FastAPI app, all routes
schemas.py         - Pydantic request/response models
database.py         - SQLAlchemy models (Device, TelemetryReading)
anomaly.py          - GPS jump / energy balance / confidence scoring
emissions.py         - E_base / E_project / E_avoided calculations
audit.py            - Tamper-evident hash chain
auth.py             - Device + admin authentication
rate_limit.py         - Per-device rate limiting
config.py           - Environment-based settings
simulator.py          - Synthetic fleet data generator
alembic/            - Database migrations
tests/              - pytest suite (unit + integration)
```
