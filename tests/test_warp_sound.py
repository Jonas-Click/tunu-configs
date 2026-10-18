"""Offline trigger-policy tests; no Redis, CAN or vehicle access."""
import importlib.util
from pathlib import Path
import tomllib
import unittest

spec = importlib.util.spec_from_file_location(
    "warp_sound", Path(__file__).resolve().parents[1] / "extensions/warp-sound.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def event(before, after):
    return {"from": before, "to": after,
            "data": {"hash": "engine-ecu", "field": "speed"}}


class WarpSoundPolicyTests(unittest.TestCase):
    def test_extension_is_disabled_and_observes_existing_fields_only(self):
        path = Path(__file__).resolve().parents[1] / "extensions/warp-sound.toml"
        rules = tomllib.loads(path.read_text())["rule"]
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["on"], ["input.warp-speed"])
        self.assertEqual(rule["input"][0]["hash"], "engine-ecu")
        self.assertEqual(rule["input"][0]["fields"], ["speed"])
        self.assertEqual(rule["input"][1]["hash"], "vehicle")
        self.assertEqual(rule["input"][1]["fields"], ["state"])

    def setUp(self):
        self.armed = {"armed": True, "last_trigger": 0.0}

    def evaluate(self, ev, current, state="ready-to-drive", saved=None, now=100.0):
        return policy.decide(ev, current, state, self.armed if saved is None else saved, now)

    def test_upward_crossing_plays_once(self):
        saved, play = self.evaluate(event("50", "50.1"), "50.1")
        self.assertTrue(play)
        self.assertFalse(saved["armed"])
        saved, play = self.evaluate(event("51", "52"), "52", saved=saved, now=110)
        self.assertFalse(play)

    def test_downward_crossing_does_not_play_and_rearms_below_45(self):
        saved, play = self.evaluate(event("51", "50"), "50")
        self.assertFalse(play)
        saved, play = self.evaluate(event("46", "45"), "45", saved={"armed": False})
        self.assertFalse(play)
        self.assertTrue(saved["armed"])

    def test_startup_above_threshold_and_unknown_from_do_not_play(self):
        for before in ("", "55"):
            saved, play = self.evaluate(event(before, "56"), "56")
            self.assertFalse(play)
            self.assertFalse(saved["armed"])

    def test_non_driving_state_does_not_play(self):
        saved, play = self.evaluate(event("50", "51"), "51", state="parked")
        self.assertFalse(play)
        self.assertFalse(saved["armed"])

    def test_stale_event_does_not_play_or_rearm(self):
        saved, play = self.evaluate(event("50", "51"), "53")
        self.assertFalse(play)
        self.assertFalse(saved["armed"])
        saved, play = self.evaluate(event("51", "44"), "53", saved={"armed": False})
        self.assertFalse(play)
        self.assertFalse(saved["armed"])

    def test_cooldown_suppresses_crossing_until_rearmed_and_expired(self):
        saved = {"armed": True, "last_trigger": 90.0}
        state, play = self.evaluate(event("50", "51"), "51", saved=saved, now=100)
        self.assertFalse(play)
        self.assertFalse(state["armed"])
        state, play = self.evaluate(event("44", "44"), "44", saved=state, now=140)
        self.assertFalse(play)
        self.assertTrue(state["armed"])
        state, play = self.evaluate(event("50", "51"), "51", saved=state, now=140)
        self.assertTrue(play)

    def test_invalid_speed_and_unrelated_field_do_not_play(self):
        self.assertFalse(self.evaluate(event("NaN", "51"), "51")[1])
        other = event("50", "51")
        other["data"]["field"] = "rpm"
        self.assertFalse(self.evaluate(other, "51")[1])


if __name__ == "__main__":
    unittest.main()
