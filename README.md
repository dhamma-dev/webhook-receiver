# README

Flask app for [Render](https://render.com), used as a **webhook receiver** for alarm events (RAISED/CLEARED) with a simple dashboard.

## POC storage

Events are stored **in memory** (no persistence across restarts). See [PLAN.md](PLAN.md) for adding Supabase or other persistent storage.

## Run locally

```bash
pip install -r requirements.txt
export DASHBOARD_USER=admin DASHBOARD_PASSWORD=yourpassword   # optional, for login
python app.py
```

- **Dashboard:** http://127.0.0.1:5000/ (login required if env vars set)
- **Login:** set **DASHBOARD_USER** and **DASHBOARD_PASSWORD** (plain text). One user only. If either is unset, login is disabled (no one can log in).
- **Health:** GET http://127.0.0.1:5000/webhook/alarms/health
- **Webhook:** POST http://127.0.0.1:5000/webhook/alarms (no auth) with JSON body (e.g. `alarmId`, `state`, `rule`, …)
- **API (for partner):** GET /api/events?org_id=&connector_id=&since=&until=&limit= (session or `X-API-Key` if **RECEIVER_API_KEY** is set)

Set `SECRET_KEY` in production for secure sessions. The dashboard auto-refreshes every 5s; check "Pause auto-refresh" to stop updates while you interact.

## Deployment

Follow the guide at https://render.com/docs/deploy-flask. Set the start command to `gunicorn app:app` (or as Render suggests). In Render Environment, set **SECRET_KEY**, **DASHBOARD_USER**, and **DASHBOARD_PASSWORD** (plain text). Optional: **RECEIVER_API_KEY** for the partner service to call `GET /api/events` with `X-API-Key` without logging in.

## OAuth + Protected API POC (new)

This repo also contains a 2-server POC to simulate an OAuth-protected API:

- **OAuth token server**: exposed from `app.py` at `POST /token` (+ discovery)
- **Protected API**: exposed from `app.py` under `/api/*`

### Run locally (single server, no custom ports)

```bash
pip install -r requirements.txt
export JWT_SECRET="dev-change-me"
python app.py
```

- App (receiver + OAuth + protected API): `http://127.0.0.1:5000`

Environment variables (shared):
- **JWT_SECRET**: shared signing secret for HS256
- **API_AUDIENCE**: JWT `aud` claim (default `protected-api`)
- **OAUTH_ISSUER**: JWT `iss` claim + discovery issuer (default uses request host)
- **TOKEN_EXPIRES_IN**: token lifetime seconds (default `3600`)

OAuth server client configuration:
- **full client**: `client_id=full-client`, secret from **OAUTH_FULL_CLIENT_SECRET** (default `full-secret`), scopes `read write admin`
- **limited client**: `client_id=limited-client`, secret from **OAUTH_LIMITED_CLIENT_SECRET** (default `limited-secret`), scope `read`

### Demo flow (curl)

Acquire a token:

```bash
curl -sS -X POST "http://127.0.0.1:5000/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data "grant_type=client_credentials&client_id=full-client&client_secret=full-secret&scope=read write" | jq .
```

Use it against the protected API:

```bash
TOKEN="$(curl -sS -X POST "http://127.0.0.1:5000/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data "grant_type=client_credentials&client_id=full-client&client_secret=full-secret&scope=read" | jq -r .access_token)"

curl -sS "http://127.0.0.1:5000/api/data" -H "Authorization: Bearer $TOKEN" | jq .
```

### Render deployment without destroying the old app

This repo includes a **Render Blueprint** at `render.yaml` that creates one new web service that serves:
- the existing receiver/dashboard routes, and
- the OAuth + protected API endpoints

Import the blueprint in Render (New → Blueprint) pointing at this repo/branch. This will **create new services**; it does not delete your existing receiver app.

Important:
- Set **JWT_SECRET**.
- (Recommended) Set **OAUTH_ISSUER** to the public Render URL of the service so `iss` is stable.

## Partner service (alarm comparison)

See **[partner/README.md](partner/README.md)**. A separate app that runs locally, fetches events from this receiver (by `org_id` + `connector_id`) and alarms from the App GraphQL API, and compares them (in both / only webhook / only API). Requires **RECEIVER_URL**, **RECEIVER_API_KEY** (if set on receiver), **APP_API_BASE_URL**, **APP_API_TOKEN**.
