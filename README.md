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

Set `SECRET_KEY` in production for secure sessions. The dashboard auto-refreshes every 5s; check "Pause auto-refresh" to stop updates while you interact.

## Deployment

Follow the guide at https://render.com/docs/deploy-flask. Set the start command to `gunicorn app:app` (or as Render suggests). In Render Environment, set **SECRET_KEY**, **DASHBOARD_USER**, and **DASHBOARD_PASSWORD** (plain text).
