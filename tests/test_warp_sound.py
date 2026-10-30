"""Offline trigger-policy tests; no Redis, CAN or vehicle access."""
import importlib.util
from pathlib import Path
import tomllib
import unittest

spec = importlib.util.spec_from_file_location(
    "warp_sound", Path(__file__).resolve().parents[1] / "extensions/warp-sound.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def event(before, after, timestamp):
    return {"from": before, "to": after, "ts": timestamp,
            "data": {"hash": "engine-ecu", "field": "speed"}}


class WarpSoundPolicyTests(unittest.TestCase):
    def test_extension_is_disabled_and_observes_existing_fields_only(self):
        path = Path(__file__).resolve().parents[1] / "extensions/warp-sound.toml"
        rules = tomllib.loads(path.read_text())["rule"]
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["on"], ["input.warp-speed"])
        self.assertEqual(rule["concurrency"], "drop")
        self.assertEqual(rule["input"][0]["hash"], "engine-ecu")
        self.assertEqual(rule["input"][0]["fields"], ["speed"])
        self.assertEqual(rule["input"][1]["hash"], "vehicle")
        self.assertEqual(rule["input"][1]["fields"], ["state"])

    def setUp(self):
        self.saved = {"armed": False, "last_trigger": 0.0}

    def evaluate(self, ev, current=None, state="ready-to-drive", saved=None, now=None):
        timestamp = ev["ts"] / 1000.0
        return policy.decide(ev, str(ev["to"] if current is None else current), state,
                             self.saved if saved is None else saved,
                             timestamp if now is None else now)

    def seed_below_rearm(self, timestamp=100000):
        return self.evaluate(event("44", "44", timestamp), "44")[0]

    def test_recent_contiguous_upward_crossing_plays_once(self):
        saved = self.seed_below_rearm()
        saved, play = self.evaluate(event("44", "50", 100200), saved=saved)
        self.assertFalse(play)
        saved, play = self.evaluate(event("50", "50.1", 100400), saved=saved)
        self.assertTrue(play)
        self.assertFalse(saved["armed"])
        saved, play = self.evaluate(event("50.1", "52", 100600), saved=saved)
        self.assertFalse(play)

    def test_first_event_and_mismatched_predecessor_cannot_trigger(self):
        saved, play = self.evaluate(event("50", "51", 100000), "51")
        self.assertFalse(play)
        saved = self.seed_below_rearm()
        saved, play = self.evaluate(event("49", "51", 100200), saved=saved)
        self.assertFalse(play)
        self.assertFalse(saved["armed"])
        self.assertIsNone(saved["baseline_speed"])

    def test_old_future_out_of_order_and_gap_events_invalidate_baseline(self):
        for first, second, now in (
            (100000, 102000, 1020.0),       # predecessor gap exceeds one second
            (100000, 99999, 100.0),         # timestamp goes backwards
            (97000, 100000, 100.0),         # old observation clears baseline
            (100251, 100000, 100.0),        # future timestamp clears baseline
        ):
            with self.subTest(first=first, second=second):
                saved = self.seed_below_rearm(first)
                rejected, play = self.evaluate(event("44", "51", second), "51",
                                               saved=saved, now=now)
                self.assertFalse(play)
                self.assertFalse(rejected["armed"])
                self.assertIsNone(rejected["baseline_speed"])
                self.assertIsNone(rejected["baseline_ts_ms"])

    def test_missing_or_invalid_fields_and_speed_mismatch_invalidate(self):
        saved = self.seed_below_rearm()
        for ev, current in (
            ({"from": "44", "to": "51", "ts": 100100,
              "data": {"hash": "engine-ecu", "field": "speed"}}, "NaN"),
            ({"from": "44", "to": "NaN", "ts": 100100,
              "data": {"hash": "engine-ecu", "field": "speed"}}, "NaN"),
            ({"from": "44", "to": "51",
              "data": {"hash": "engine-ecu", "field": "speed"}}, "51"),
        ):
            rejected, play = policy.decide(ev, current, "ready-to-drive", saved, 100.1)
            self.assertFalse(play)
            self.assertFalse(rejected["armed"])
            self.assertIsNone(rejected["baseline_speed"])

    def test_stale_event_cannot_rearm(self):
        saved = {"armed": False, "last_trigger": 0, "baseline_speed": 60,
                 "baseline_ts_ms": 99000}
        updated, play = self.evaluate(event("60", "44", 97000), "44",
                                      saved=saved, now=100)
        self.assertFalse(play)
        self.assertFalse(updated["armed"])
        self.assertIsNone(updated["baseline_speed"])

    def test_downward_observation_rearms_and_cooldown_is_preserved(self):
        saved = {"armed": False, "last_trigger": 90.0,
                 "baseline_speed": 51.0, "baseline_ts_ms": 100000}
        saved, play = self.evaluate(event("51", "44", 100100), saved=saved)
        self.assertFalse(play)
        self.assertTrue(saved["armed"])
        saved["last_trigger"] = 100.0
        saved, play = self.evaluate(event("44", "51", 100200), saved=saved, now=100.2)
        self.assertFalse(play)
        self.assertFalse(saved["armed"])

    def test_non_driving_state_does_not_play(self):
        saved = self.seed_below_rearm()
        saved, play = self.evaluate(event("44", "51", 100100), "51", state="parked", saved=saved)
        self.assertFalse(play)
        self.assertFalse(saved["armed"])

    def test_event_timestamps_are_observation_time_not_ecu_time(self):
        self.assertEqual(policy.MAX_EVENT_AGE_SECONDS, 1.0)
        self.assertEqual(policy.MAX_BASELINE_GAP_SECONDS, 1.0)
        self.assertEqual(policy.MAX_FUTURE_SKEW_SECONDS, 0.25)


if __name__ == "__main__":
    unittest.main()
