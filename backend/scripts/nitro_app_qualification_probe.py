"""Prepared task command; NOT executed during read-only qualification preparation."""

import hashlib
import json
import os
import subprocess
import time
from urllib.parse import urlsplit

import requests

# A wrongly released negative clone must never callback as the real App task.
if os.environ.get("QUALIFICATION_PUBLIC_VARIANT") == "negative":
    raise SystemExit(70)
if (os.getuid(), os.getgid()) != (42042, 42042):
    raise SystemExit(71)
for name in [
    "AGENT_CALLBACK_URL",
    "ASTROLIFT_CONTROLLER_URL",
    "ASTROLIFT_MESSAGES_URL",
    "ASTROLIFT_RUN_MCP_URL",
]:
    if urlsplit(os.environ[name]).scheme != "https":
        raise SystemExit(72)
packet = json.loads(os.environ["ASTROLIFT_TRIGGER_PAYLOAD"])
secret_hash = hashlib.sha256(os.environ["ASTROLIFT_QUALIFICATION_SECRET"].encode()).hexdigest()
if secret_hash != packet["expected_secret_sha256"]:
    raise SystemExit(73)
if not os.environ.get("ASTROLIFT_BRIEF_ID") or not os.environ.get("ASTROLIFT_BRIEF_HASH"):
    raise SystemExit(74)
# Verify the exact image's baked contract as the actual non-root guest. Never emit output.
contract = subprocess.run(
    ["/usr/local/bin/astrolift-agent-contract", "base"], capture_output=True, timeout=90
)
if contract.returncode:
    raise SystemExit(75)
report = {
    "qualification": packet["qualification_id"],
    "sealed_inputs_ok": True,
    "uid": os.getuid(),
    "gid": os.getgid(),
    "secret_sha256": secret_hash,
    "brief_id": os.environ["ASTROLIFT_BRIEF_ID"],
    "brief_hash": os.environ["ASTROLIFT_BRIEF_HASH"],
    "system_sha256": hashlib.sha256(os.environ.get("AGENT_SYSTEM", "").encode()).hexdigest(),
}
url = os.environ["AGENT_CALLBACK_URL"]
controller = urlsplit(os.environ["ASTROLIFT_CONTROLLER_URL"])
if (
    urlsplit(url).netloc != controller.netloc
    or urlsplit(url).path != "/api/dispatch/v1/agents/" + os.environ["ASTROLIFT_TASK_ID"] + "/callback/"
):
    raise SystemExit(76)


def callback(payload):
    deadline = time.monotonic() + 90
    while True:
        response = requests.post(
            url,
            json=payload,
            headers={"Authorization": "Bearer " + os.environ["ASTROLIFT_CLUSTER_KEY"]},
            timeout=15,
            allow_redirects=False,
        )
        # The guest can start before the App poll records RUNNING.
        if (
            response.status_code == 409
            and payload.get("finding")
            and response.json().get("error", "").startswith("task is not accepting findings")
            and time.monotonic() < deadline
        ):
            time.sleep(5)
            continue
        if not 200 <= response.status_code < 300:
            raise SystemExit(77)
        return response.json()


hold = int(packet.get("hold_seconds", 900))
if not 60 <= hold <= 1200:
    raise SystemExit(78)
callback({"status": "running", "finding": report})
deadline = time.monotonic() + hold
while time.monotonic() < deadline:
    reply = callback({"task_id": os.environ["ASTROLIFT_TASK_ID"], "status": "running"})
    if reply.get("continue") is False:
        raise SystemExit(79)
    time.sleep(min(15, max(0, deadline - time.monotonic())))
callback(
    {
        "task_id": os.environ["ASTROLIFT_TASK_ID"],
        "status": "completed",
        "ok": True,
        "stop_reason": "qualification_complete",
        "result": report,
    }
)
