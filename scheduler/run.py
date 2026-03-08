import os
import time
import requests

API_BASE = os.environ.get("ICEA_API_BASE", "http://web:8000/api/v1").rstrip("/")
INTERVAL_S = int(os.environ.get("ICEA_TRAIN_INTERVAL_S", str(24 * 3600)))
BUILD_WINDOWS = str(os.environ.get("ICEA_BUILD_WINDOWS", "false")).lower() in {"1", "true", "yes"}
WINDOW_HOURS = int(os.environ.get("ICEA_WINDOW_HOURS", "12"))
WINDOW_ALIGN = os.environ.get("ICEA_WINDOW_ALIGN", "shift")


def post(path, payload=None):
    url = f"{API_BASE}/{path.lstrip('/')}"
    r = requests.post(url, json=payload or {}, timeout=60)
    r.raise_for_status()
    return r.json()


while True:
    try:
        # Build dataset for all episodes (idempotent)
        post("pipeline/build-dataset/", {"truncate": False})

        # Optional: window-grain dataset (target-trial emulation)
        if BUILD_WINDOWS:
            post("pipeline/build-windows/", {"truncate": False, "window_hours": WINDOW_HOURS, "align": WINDOW_ALIGN})

        # Train from DB (episode grain)
        post("pipeline/train/", {"name": "icea-xgb", "version": "v0.5"})
        print("[scheduler] training cycle OK")
    except Exception as e:
        print(f"[scheduler] training cycle FAILED: {e}")

    time.sleep(INTERVAL_S)
