"""Request a stable raw ZIP from the running local application."""

import argparse
import uuid
from pathlib import Path

import httpx

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("session_id")
    args = p.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            f"/api/sessions/{args.session_id}/exports",
            json={"format": "raw"},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        response.raise_for_status()
        result = response.json()
        out = Path("data/exports") / f"{args.session_id}.zip"
        out.write_bytes(client.get(result["url"]).content)
        print(out.resolve())
