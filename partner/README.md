# Partner service: alarm comparison

Runs **locally** and compares alarms from:

1. **Webhook receiver** (public URL) — events received via webhook, filtered by `org_id` + `connector_id`
2. **App API** (GraphQL) — alarms from the same org in the same date range

Matching is by **alarm UUID**: webhook `alarmId` ↔ API `externalAlarmId`. Segment is **org_id + connector_id**.

## Env vars

| Variable | Description |
|----------|-------------|
| `RECEIVER_URL` | Base URL of the webhook receiver (e.g. `https://your-app.onrender.com`) |
| `RECEIVER_API_KEY` | Optional. If the receiver has `RECEIVER_API_KEY` set, use the same value here for `GET /api/events` |
| `APP_API_BASE_URL` | App API base (e.g. `https://demo.pm.appneta.com`) |
| `APP_API_TOKEN` | Token for `Authorization: Token <APP_API_TOKEN>` |

## Receiver setup

On the receiver, set **RECEIVER_API_KEY** (optional). Then the partner can call `GET /api/events?org_id=&connector_id=&since=&until=` with header `X-API-Key: <key>` without logging in.

## Run

```bash
cd partner
pip install -r requirements.txt
export RECEIVER_URL=https://your-receiver.onrender.com RECEIVER_API_KEY=secret
export APP_API_BASE_URL=https://demo.pm.appneta.com APP_API_TOKEN=xxx
python app.py
```

Open http://127.0.0.1:5001/, enter **Org ID**, **Connector ID**, and **date range** (ISO), then click **Compare**. Results: **In both**, **Only in webhook**, **Only in API**.
