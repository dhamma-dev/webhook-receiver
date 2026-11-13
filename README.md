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

Set `SECRET_KEY` in production for secure sessions.

## Console

- **Alarms** – one row per `alarmId` (RAISED and CLEARED correlated), severity tiles, event-rate chart, search (`/`), severity and state filters, acknowledge, and a detail drawer with the event timeline and payloads.
- **Event log** – every webhook received, newest first.
- **Integration** – endpoint URLs, payload field reference, a curl sample, and a test sender ("Simulate burst" raises a handful of sample alarms).

The console refreshes every 4s; click the **Live** pill to pause. Acknowledgements are in memory, like the events.

## Deployment

Follow the guide at https://render.com/docs/deploy-flask. Set the start command to `gunicorn app:app` (or as Render suggests). In Render Environment, set **SECRET_KEY**, **DASHBOARD_USER**, and **DASHBOARD_PASSWORD** (plain text).
