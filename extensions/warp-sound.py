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
SONG_NUMBER = 7
STATE_KEY = "tunu:warp-sound:trigger-state"


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def decide(event, current_speed, vehicle_state, saved, now):
    """Return (next state, should publish); event values are observed strings."""
    data = event.get("data")
    if not isinstance(data, dict) or data.get("hash") != "engine-ecu" or data.get("field") != "speed":
        return saved, False
    timestamp = number(event.get("ts"))
    if timestamp is None:
        return saved, False
    age = now - timestamp / 1000.0
    if age < -MAX_FUTURE_SKEW_SECONDS or age > MAX_EVENT_AGE_SECONDS:
        return saved, False
    before = number(event.get("from"))
    after = number(event.get("to"))
    current = number(current_speed)
    if after is None:
        return saved, False
    state = {"armed": bool(saved.get("armed", False)),
             "last_trigger": number(saved.get("last_trigger")) or 0.0}
    if current is None or abs(current - after) > 0.01:
        if current is not None and current > THRESHOLD_KMH:
            state["armed"] = False
        return state, False

    if after <= REARM_KMH:
        state["armed"] = True
        return state, False
    if after <= THRESHOLD_KMH:
        return state, False

    # A first live observation, startup-above-threshold value or stale queued
    # transition must never be mistaken for an observed crossing.
    if vehicle_state != "ready-to-drive":
        state["armed"] = False
        return state, False
    crossed_up = before is not None and before <= THRESHOLD_KMH < after
    should_play = (crossed_up and state["armed"]
                   and now - state["last_trigger"] >= COOLDOWN_SECONDS)
    state["armed"] = False
    if should_play:
        state["last_trigger"] = now
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
