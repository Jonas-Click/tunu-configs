"""Battery-current event reconciliation without Redis, CAN or hardware."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'battery_current', Path(__file__).resolve().parents[1] / 'extensions/battery-current.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

DUAL = ['on', 'true', 'active', 'true', 'active']
SINGLE = ['on', 'true', 'active', 'false', 'unknown']
OFF = ['off', 'true', 'active', 'true', 'active']


def reply(amps=150, watts=8000):
    return ['("TUNU-LIMITS" %s.000000f32 %s.000000f32)' % (amps, watts), '> t']


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.clock = [0.0]
        self.connection = Mock()
        self.transport = SimpleNamespace(VescCan=Mock(return_value=self.connection), cmd_repl=Mock())
        self.state = self.enterContext(patch.object(policy, 'read_state', return_value=DUAL))
        self.report = self.enterContext(patch.object(policy, 'report'))
        self.enterContext(patch.object(policy.time, 'monotonic', side_effect=lambda: self.clock[0]))
        self.enterContext(patch.object(policy.time, 'sleep', side_effect=self.sleep))

    def sleep(self, seconds):
        self.clock[0] += seconds

    def expressions(self):
        return [c.args[1] for c in self.transport.cmd_repl.call_args_list]

    def test_only_two_active_present_packs_get_150(self):
        self.assertEqual(policy.target_current(DUAL), 150)
        for index, value in [(1, 'false'), (2, 'asleep'), (3, 'false'), (4, 'asleep')]:
            state = DUAL.copy()
            state[index] = value
            self.assertEqual(policy.target_current(state), 80)

    def test_engine_off_never_opens_can(self):
        self.state.return_value = OFF
        policy.reconcile(self.transport)
        self.transport.VescCan.assert_not_called()

    def test_matching_limits_do_not_write(self):
        self.transport.cmd_repl.return_value = reply()
        policy.reconcile(self.transport, 8000)
        self.assertEqual(self.expressions(), [policy.READBACK])
        self.transport.VescCan.assert_called_once_with('can0', 70, 251, 1.0)
        self.connection.close.assert_called_once()

    def test_boot_readiness_retried_before_write(self):
        self.transport.cmd_repl.side_effect = [[], [], reply(60, 5000), reply()]
        policy.reconcile(self.transport, 8000)
        self.assertEqual(self.expressions()[:3], [policy.READBACK] * 3)
        written = self.expressions()[-1]
        self.assertIn("(conf-set 'l-in-current-max 150.0)", written)
        self.assertIn("(conf-set 'l-watt-max 8000.0)", written)
        self.assertNotIn("conf-store", written)
        self.assertNotIn("l-current-max", written)
        self.assertNotIn("l-in-current-min", written)
        self.assertEqual(self.clock[0], 2.0)

    def test_unspecified_power_limit_is_preserved(self):
        self.transport.cmd_repl.side_effect = [reply(60, 5000), reply(150, 5000)]
        policy.reconcile(self.transport)
        self.assertNotIn("(conf-set 'l-watt-max", self.expressions()[-1])

    def test_engine_off_during_readiness_prevents_write(self):
        self.state.side_effect = [DUAL, OFF]
        self.transport.cmd_repl.return_value = reply(60)
        policy.reconcile(self.transport)
        self.assertEqual(self.expressions(), [policy.READBACK])

    def test_pack_drop_during_readiness_uses_lower_limit(self):
        self.state.side_effect = [DUAL, SINGLE, SINGLE]
        self.transport.cmd_repl.side_effect = [reply(60), reply(80)]
        policy.reconcile(self.transport)
        self.assertIn("(conf-set 'l-in-current-max 80.0)", self.expressions()[-1])

    def test_pack_drop_after_write_reconciles_again(self):
        self.state.side_effect = [DUAL, DUAL, SINGLE, SINGLE, SINGLE, SINGLE]
        self.transport.cmd_repl.side_effect = [reply(60), reply(150), reply(150), reply(80)]
        policy.reconcile(self.transport)
        self.assertIn("(conf-set 'l-in-current-max 150.0)", self.expressions()[1])
        self.assertIn("(conf-set 'l-in-current-max 80.0)", self.expressions()[3])

    def test_no_reply_fails_within_deadline_and_closes_socket(self):
        self.transport.cmd_repl.return_value = []
        with self.assertRaisesRegex(RuntimeError, 'not confirmed within 3s'):
            policy.reconcile(self.transport, duration=3)
        self.assertEqual(self.clock[0], 3)
        self.assertEqual(self.expressions(), [policy.READBACK] * 3)
        self.connection.close.assert_called_once()

    def test_unrelated_repl_output_is_not_confirmation(self):
        self.transport.cmd_repl.return_value = ['> 150.0', '> t']
        with self.assertRaisesRegex(RuntimeError, 'readback missing'):
            policy.reconcile(self.transport, duration=1)

    def test_wrong_write_readback_is_retried(self):
        self.transport.cmd_repl.side_effect = [reply(60), reply(60), reply(60), reply(150)]
        policy.reconcile(self.transport)
        self.assertEqual(self.clock[0], 1)
        self.assertEqual(len(self.expressions()), 4)

    def test_can_open_failure_is_retried(self):
        self.transport.VescCan.side_effect = [OSError('CAN unavailable'), self.connection]
        self.transport.cmd_repl.return_value = reply()
        policy.reconcile(self.transport)
        self.assertEqual(self.transport.VescCan.call_count, 2)
        self.assertEqual(self.clock[0], 1)

    def test_configured_dual_current_and_power(self):
        self.transport.cmd_repl.side_effect = [reply(150, 5500), reply(120, 7000)]
        policy.reconcile(self.transport, 7000, dual_amps=120)
        self.assertIn("(conf-set 'l-in-current-max 120.0)", self.expressions()[-1])
        self.assertIn("(conf-set 'l-watt-max 7000.0)", self.expressions()[-1])
        self.assertEqual(policy.target_current(SINGLE, 120), 80)

    def test_configured_limit_survives_pack_change(self):
        self.state.side_effect = [DUAL, DUAL, SINGLE, SINGLE, SINGLE, SINGLE]
        self.transport.cmd_repl.side_effect = [reply(150), reply(120), reply(120), reply(80)]
        policy.reconcile(self.transport, dual_amps=120)
        self.assertIn("(conf-set 'l-in-current-max 120.0)", self.expressions()[1])
        self.assertIn("(conf-set 'l-in-current-max 80.0)", self.expressions()[3])

    def test_power_only_change_is_applied(self):
        self.transport.cmd_repl.side_effect = [reply(150, 5000), reply()]
        policy.reconcile(self.transport, 8000)
        self.assertIn("(conf-set 'l-watt-max 8000.0)", self.expressions()[-1])

    def test_guarded_reconciliation_preserves_the_active_boost_allowance(self):
        self.transport.cmd_repl.side_effect = [reply(120, 7500), reply(120, 7500)]
        policy.reconcile(self.transport, 7500, dual_amps=120, boost_guard=True)
        self.assertEqual(self.expressions()[0], policy.GUARDED_READBACK)
        self.assertIn('(boost-set-normal-current 120.0 3)', self.expressions()[1])
        self.assertNotIn("conf-set 'l-in-current-max", self.expressions()[1])

    def test_guarded_missing_api_does_not_fall_back_to_direct_writes(self):
        self.transport.cmd_repl.return_value = ['> variable_not_bound']
        with self.assertRaises(RuntimeError):
            policy.reconcile(self.transport, duration=1, boost_guard=True)
        self.assertEqual(self.expressions(), [policy.GUARDED_READBACK])

    def test_guarded_mode_revokes_pack_evidence_even_with_equal_normal_caps(self):
        self.state.return_value = SINGLE
        self.transport.cmd_repl.side_effect = [reply(60), reply(60)]
        policy.reconcile(self.transport, single_amps=60, boost_guard=True)
        self.assertIn('(boost-set-normal-current 60.0 1)', self.expressions()[1])

    def test_single_current_can_preserve_a_sixty_amp_installation(self):
        self.state.return_value = SINGLE
        self.transport.cmd_repl.return_value = reply(60)
        policy.reconcile(self.transport, single_amps=60)
        self.assertEqual(self.expressions(), [policy.READBACK])


class ConfigTests(unittest.TestCase):
    def test_optional_power_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'limits.json'
            self.assertEqual(policy.load_limits(path), (None, 150))
            for value in (None, 8000):
                path.write_text(json.dumps({'power_max_w': value}))
                self.assertEqual(policy.load_limits(path), (value, 150))
            for value in (True, '8000', 0, -1, float('inf'), float('nan')):
                path.write_text(json.dumps({'power_max_w': value}))
                with self.assertRaises(ValueError):
                    policy.load_limits(path)
            for value in (80, 120, 150):
                path.write_text(json.dumps({'power_max_w': 7000, 'dual_current_a': value}))
                self.assertEqual(policy.load_limits(path), (7000, value))
            for value in (None, True, '120', 79, 151, float('nan')):
                path.write_text(json.dumps({'dual_current_a': value}))
                with self.assertRaises(ValueError):
                    policy.load_limits(path)
            path.write_text(json.dumps({'single_current_a': 60, 'boost_guard': True}))
            self.assertEqual(policy.load_boost_policy(path), (None, 150, 60, True))
            for value in (0, 86, True, '60', float('nan')):
                path.write_text(json.dumps({'single_current_a': value}))
                with self.assertRaises(ValueError):
                    policy.load_limits(path)
            path.write_text('{"boost_guard": 1}')
            with self.assertRaises(ValueError):
                policy.load_limits(path)
            for text in ('{"power_typo": 8000}', '[]', 'null'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    policy.load_limits(path)


if __name__ == '__main__':
    unittest.main()
