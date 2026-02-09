"""
Partner service: compare webhook-received alarms (from receiver) vs API-polled alarms (from App).
Segmented by org_id + connector_id. Run locally; calls receiver and App API.
"""
import os
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, request, render_template

app = Flask(__name__)

RECEIVER_URL_ENV = "RECEIVER_URL"
RECEIVER_API_KEY_ENV = "RECEIVER_API_KEY"
APP_API_BASE_ENV = "APP_API_BASE_URL"
APP_API_TOKEN_ENV = "APP_API_TOKEN"

# GraphQL query from App (AlarmsTableData)
ALARMS_QUERY = """
query AlarmsTableData($filter: AlarmFilter, $orderBy: [AlarmOrder]) {
  alarms(filter: $filter, orderBy: $orderBy) {
    alarmId
    alarmDetectedTime
    clearedTime
    duration
    externalAlarmId
    incidentId
    itemId
    tenantId
    itemName
    itemType
    monitoringPoint
    monitoringPointType
    monitoringPolicyName
    policyGroup
    raisedTime
    severity
    status
    shelves
    target
    tags { category value }
    title
  }
}
"""


def fetch_receiver_events(org_id: str, connector_id: str, since: str, until: str, limit: int = 2000):
    base = os.environ.get(RECEIVER_URL_ENV, "").rstrip("/")
    key = os.environ.get(RECEIVER_API_KEY_ENV)
    if not base:
        return None, "RECEIVER_URL not set"
    url = f"{base}/api/events"
    params = {"org_id": org_id, "connector_id": connector_id, "since": since, "until": until, "limit": limit}
    headers = {"Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json(), None
    except requests.RequestException as e:
        return None, str(e)


def fetch_app_alarms(org_id: str, date_from: str, date_to: str):
    base = os.environ.get(APP_API_BASE_ENV, "").rstrip("/")
    token = os.environ.get(APP_API_TOKEN_ENV)
    if not base:
        return None, "APP_API_BASE_URL not set"
    if not token:
        return None, "APP_API_TOKEN not set"
    url = f"{base}/api/internal/alarm/graphql"
    params = {"orgId": org_id, "op": "AlarmsTableData"}
    headers = {
        "Accept": "application/graphql-response+json, application/json",
        "Content-Type": "application/json",
        "Authorization": f"Token {token}",
    }
    payload = {
        "query": ALARMS_QUERY,
        "variables": {
            "orgId": org_id,
            "orderBy": [{"sortField": "ALARM_RAISED_TIME", "sortOrder": "DESC"}],
            "filter": {
                "shelves": {"operator": "NE", "value": "frequent_alarm"},
                "dateRange": {"operator": "between", "value": [date_from, date_to]},
            },
        },
        "operationName": "AlarmsTableData",
    }
    try:
        r = requests.post(url, params=params, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        alarms = (data.get("data") or {}).get("alarms") or []
        return alarms, None
    except requests.RequestException as e:
        return None, str(e)


def compare(webhook_events: list, api_alarms: list):
    """Produce sets and lists keyed by alarm UUID (webhook alarmId <-> API externalAlarmId)."""
    webhook_ids = set()
    webhook_by_id = {}
    for ev in webhook_events or []:
        payload = ev.get("payload") or {}
        aid = payload.get("alarmId")
        if aid:
            aid = str(aid).strip()
            webhook_ids.add(aid)
            if aid not in webhook_by_id:
                webhook_by_id[aid] = []
            webhook_by_id[aid].append(ev)

    api_ids = set()
    api_by_id = {}
    for al in api_alarms or []:
        eid = al.get("externalAlarmId")
        if eid:
            eid = str(eid).strip()
            api_ids.add(eid)
            api_by_id[eid] = al

    in_both_ids = webhook_ids & api_ids
    only_webhook_ids = webhook_ids - api_ids
    only_api_ids = api_ids - webhook_ids

    in_both = [{"id": i, "webhook_events": webhook_by_id[i], "api_alarm": api_by_id[i]} for i in in_both_ids]
    only_webhook = [{"id": i, "webhook_events": webhook_by_id[i]} for i in only_webhook_ids]
    only_api = [{"id": i, "api_alarm": api_by_id[i]} for i in only_api_ids]

    return {
        "in_both": in_both,
        "only_webhook": only_webhook,
        "only_api": only_api,
        "count_in_both": len(in_both_ids),
        "count_only_webhook": len(only_webhook_ids),
        "count_only_api": len(only_api_ids),
    }


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    err = None
    if request.method == "POST":
        org_id = (request.form.get("org_id") or "").strip()
        connector_id = (request.form.get("connector_id") or "").strip()
        date_from = (request.form.get("date_from") or "").strip()
        date_to = (request.form.get("date_to") or "").strip()
        if not org_id or not connector_id:
            err = "org_id and connector_id are required"
        elif not date_from or not date_to:
            err = "date_from and date_to are required (e.g. 2026-02-09T17:55:00, 2026-02-09T18:56:00)"
        else:
            # Receiver uses received_at; use same window for consistency
            since = date_from
            until = date_to
            recv_data, recv_err = fetch_receiver_events(org_id, connector_id, since, until)
            if recv_err:
                err = f"Receiver: {recv_err}"
            else:
                api_alarms, api_err = fetch_app_alarms(org_id, date_from, date_to)
                if api_err:
                    err = f"App API: {api_err}"
                else:
                    events = (recv_data or {}).get("events") or []
                    result = compare(events, api_alarms)
                    result["org_id"] = org_id
                    result["connector_id"] = connector_id
                    result["date_from"] = date_from
                    result["date_to"] = date_to
                    result["webhook_total"] = len(events)
                    result["api_total"] = len(api_alarms or [])

    # Default date range: last 24h
    now = datetime.now(timezone.utc)
    default_end = now.strftime("%Y-%m-%dT%H:%M:%S")
    default_start = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    return render_template(
        "compare.html",
        result=result,
        error=err,
        default_date_from=default_start,
        default_date_to=default_end,
        receiver_url=os.environ.get(RECEIVER_URL_ENV, ""),
        app_base=os.environ.get(APP_API_BASE_ENV, ""),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
