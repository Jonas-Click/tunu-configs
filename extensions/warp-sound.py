#!/usr/bin/env python3
"""Disabled-by-default speed-crossing handler for the private warp sound."""
import json
import math
import subprocess
import sys
import time

THRESHOLD_KMH = 50.0
REARM_KMH = 45.0
COOLDOWN_SECONDS = 45.0
MAX_EVENT_AGE_SECONDS = 1.0
MAX_FUTURE_SKEW_SECONDS = 0.25
MAX_BASELINE_GAP_SECONDS = 1.0
SONG_NUMBER = 240
STATE_KEY = "tunu:warp-sound:trigger-state"


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalized_state(saved):
    return {
        "armed": bool(saved.get("armed", False)),
        "last_trigger": number(saved.get("last_trigger")) or 0.0,
        "baseline_speed": number(saved.get("baseline_speed")),
        "baseline_ts_ms": number(saved.get("baseline_ts_ms")),
    }


def decide(event, current_speed, vehicle_state, saved, now):
    """Return (next state, should publish), using event-emission observations."""
    data = event.get("data")
    if not isinstance(data, dict) or data.get("hash") != "engine-ecu" or data.get("field") != "speed":
        return saved, False
    state = normalized_state(saved)

    def invalidate():
        state["armed"] = False
        state["baseline_speed"] = None
        state["baseline_ts_ms"] = None
        return state, False

    timestamp = number(event.get("ts"))
    before = number(event.get("from"))
    after = number(event.get("to"))
    current = number(current_speed)
    if timestamp is None or before is None or after is None or current is None:
        return invalidate()
    age = now - timestamp / 1000.0
    if age < -MAX_FUTURE_SKEW_SECONDS or age > MAX_EVENT_AGE_SECONDS:
        return invalidate()
    if abs(current - after) > 0.01:
        return invalidate()

    baseline_speed = state["baseline_speed"]
    baseline_ts = state["baseline_ts_ms"]
    if baseline_speed is None or baseline_ts is None:
        state["armed"] = after <= REARM_KMH
        state["baseline_speed"] = after
        state["baseline_ts_ms"] = timestamp
        return state, False
    if (timestamp <= baseline_ts
            or timestamp - baseline_ts > MAX_BASELINE_GAP_SECONDS * 1000.0
            or abs(before - baseline_speed) > 0.01):
        return invalidate()

    should_play = False
    if after <= REARM_KMH:
        state["armed"] = True
    elif after > THRESHOLD_KMH:
        crossed_up = before <= THRESHOLD_KMH
        should_play = (
            crossed_up and state["armed"]
            and vehicle_state == "ready-to-drive"
            and now - state["last_trigger"] >= COOLDOWN_SECONDS
        )
        state["armed"] = False
        if should_play:
            state["last_trigger"] = now

    state["baseline_speed"] = after
    state["baseline_ts_ms"] = timestamp
    return state, should_play


def redis(*args):
    result = subprocess.run(["redis-cli", "--raw", *args], check=True,
                            capture_output=True, text=True, timeout=2)
    return result.stdout.strip()


def main():
    event = json.load(sys.stdin)
    speed = redis("HGET", "engine-ecu", "speed")
    vehicle_state = redis("HGET", "vehicle", "state")
    raw = redis("GET", STATE_KEY)
    saved = json.loads(raw) if raw else {"armed": False, "last_trigger": 0}
    updated, should_play = decide(event, speed, vehicle_state, saved, time.time())
    if updated != saved:
        redis("SET", STATE_KEY, json.dumps(updated, separators=(",", ":")))
    if should_play:
        redis("PUBLISH", "music", "play:%d" % SONG_NUMBER)


if __name__ == "__main__":
    main()
