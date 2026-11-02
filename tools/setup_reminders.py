"""One-off: create the daily cream and dawai reminders on cron-job.org.

Runs in GitHub Actions with the CRONJOB_API_KEY secret. Safe to run again:
jobs with the same title are updated instead of duplicated.
"""

import os
import sys

import requests

API = "https://api.cron-job.org"
TOPIC_URL = "https://ntfy.sh/babeysdawaicream"
TIMEZONE = "Asia/Dubai"
POST = 1  # cron-job.org request method code for POST

REMINDERS = [
    {
        "title": "Babey morning reminder",
        "hour": 7,
        "minute": 0,
        "heading": "Morning reminder",
        "body": "Good morning. Remind babey to put cream and take dawai.",
    },
    {
        "title": "Babey night reminder",
        "hour": 21,
        "minute": 30,
        "heading": "Night reminder",
        "body": "Good night. Remind babey to put cream and take dawai.",
    },
]


def job_payload(r):
    return {
        "job": {
            "url": TOPIC_URL,
            "enabled": True,
            "title": r["title"],
            "saveResponses": True,
            "requestMethod": POST,
            "schedule": {
                "timezone": TIMEZONE,
                "expiresAt": 0,
                "hours": [r["hour"]],
                "minutes": [r["minute"]],
                "mdays": [-1],
                "months": [-1],
                "wdays": [-1],
            },
            "extendedData": {
                "headers": {"Title": r["heading"], "Priority": "high"},
                "body": r["body"],
            },
        }
    }


def main():
    key = os.environ.get("CRONJOB_API_KEY")
    if not key:
        print("CRONJOB_API_KEY secret is not set")
        return 1
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    response = session.get(f"{API}/jobs", timeout=30)
    print("list jobs:", response.status_code)
    response.raise_for_status()
    existing = {j.get("title"): j["jobId"] for j in response.json().get("jobs", [])}
    # Titles and enabled state only: other jobs' headers hold the GitHub token.
    for job in response.json().get("jobs", []):
        print(f"  existing job: {job.get('title')!r} enabled={job.get('enabled')}")

    for r in REMINDERS:
        payload = job_payload(r)
        if r["title"] in existing:
            job_id = existing[r["title"]]
            response = session.patch(f"{API}/jobs/{job_id}", json=payload, timeout=30)
            action = "updated"
        else:
            response = session.put(f"{API}/jobs", json=payload, timeout=30)
            action = "created"
        print(f"{action} {r['title']!r}:", response.status_code, response.text[:200])
        response.raise_for_status()
        job_id = existing.get(r["title"]) or response.json()["jobId"]

        check = session.get(f"{API}/jobs/{job_id}", timeout=30)
        check.raise_for_status()
        job = check.json()["jobDetails"]
        s = job["schedule"]
        print(
            f"  check: url={job['url']} method={job['requestMethod']} enabled={job['enabled']} "
            f"time={s['hours']}:{s['minutes']} tz={s['timezone']}"
        )

    test = requests.post(
        TOPIC_URL,
        data="Test: daily reminders are set for 7:00 AM and 9:30 PM.".encode(),
        headers={"Title": "Reminders set up", "Priority": "high"},
        timeout=30,
    )
    print("test notification:", test.status_code)
    test.raise_for_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
