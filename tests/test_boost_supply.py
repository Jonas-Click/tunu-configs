from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

spec = importlib.util.spec_from_file_location(
    'boost_supply', Path(__file__).resolve().parents[1] / 'extensions/boost-supply.py')
supply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supply)


def snapshot(current='0', second_current='0'):
    return ['on', 'true', 'active', '55000', current, 'pack0', '0', 'ideal', 'false',
            'true', 'active', '55000', second_current, 'pack1', '0', 'ideal', 'false']


class SupplyObservationTests(unittest.TestCase):
    def test_initial_and_unchanged_data_cannot_authorize_boost(self):
        tracker = supply.Observations()
        self.assertEqual(tracker.observe(snapshot(), 10), 0)
        self.assertEqual(tracker.observe(snapshot(), 11), 0)

    def test_only_changed_active_packs_are_leased(self):
        tracker = supply.Observations()
        tracker.observe(snapshot(), 10)
        self.assertEqual(tracker.observe(snapshot('100'), 11), 1)
        self.assertEqual(tracker.observe(snapshot('100', '100'), 11.1), 3)
        self.assertEqual(tracker.observe(snapshot('100', '100'), 13), 2)
        self.assertEqual(tracker.observe(snapshot('100', '100'), 13.1), 0)

    def test_departure_and_fault_revoke_immediately(self):
        for index, value in ((1, 'false'), (2, 'idle'), (6, '1'),
                             (7, 'hot'), (8, 'true'), (3, 'NaN')):
            with self.subTest(index=index, value=value):
                tracker = supply.Observations()
                tracker.observe(snapshot(), 10)
                self.assertEqual(tracker.observe(snapshot('100'), 11), 1)
                values = snapshot('100')
                values[index] = value
                self.assertEqual(tracker.observe(values, 11.1), 0)

    def test_pack_identity_change_needs_another_observation(self):
        tracker = supply.Observations()
        tracker.observe(snapshot(), 10)
        tracker.observe(snapshot('100'), 11)
        values = snapshot('200')
        values[5] = 'replacement'
        self.assertEqual(tracker.observe(values, 11.1), 0)
        values[4] = '300'
        self.assertEqual(tracker.observe(values, 11.2), 1)

    def test_backward_time_never_looks_fresh(self):
        tracker = supply.Observations()
        tracker.observe(snapshot(), 10)
        tracker.observe(snapshot('100'), 11)
        self.assertEqual(tracker.observe(snapshot('100'), 10), 0)

    def test_bad_snapshot_is_rejected(self):
        with self.assertRaises(RuntimeError):
            supply.Observations().observe([], 1)

    def test_service_is_opt_in_not_an_event_worker_loop(self):
        path = Path(__file__).resolve().parents[1] / 'extensions/tunu-boost-supply.service'
        self.assertIn('ExecStart=/usr/bin/python3 /data/extensions/boost-supply.py', path.read_text())
        self.assertFalse(list(path.parent.glob('*boost*.toml')))


class NativeReadinessTests(unittest.TestCase):
    def test_only_fresh_native_guard_evidence_authorizes_can_queries(self):
        base = {'version': 1, 'valid': True, 'heartbeat_fresh': True,
                'observed_at': datetime.fromtimestamp(999.8, timezone.utc).isoformat()}
        with patch.object(supply.time, 'time', return_value=1000), patch.object(supply.subprocess, 'run') as run:
            run.return_value.stdout = json.dumps(base)
            self.assertTrue(supply.native_ready())
            for changes in ({'valid': False}, {'heartbeat_fresh': False}, {'version': True},
                            {'version': 2}, {'observed_at': 'invalid'},
                            {'observed_at': datetime.fromtimestamp(999, timezone.utc).isoformat()},
                            {'observed_at': datetime.fromtimestamp(1001, timezone.utc).isoformat()}):
                run.return_value.stdout = json.dumps({**base, **changes})
                self.assertFalse(supply.native_ready(), changes)
            for raw in ('', 'null', '[]', '{', 'X' * 2049):
                run.return_value.stdout = raw
                self.assertFalse(supply.native_ready(), raw[:20])


class LeaseRenewalTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock()
        self.transport = SimpleNamespace(
            VescCan=Mock(return_value=self.connection),
            cmd_version=Mock(return_value={'major': 7, 'minor': 1, 'hw_name': 'TUNU'}),
            cmd_repl=Mock(return_value=['("BOOST-SUPPLY" 0 120.0f32)']))
        self.policy = SimpleNamespace(
            load_boost_policy=Mock(return_value=(7500, 120, 60, True)), SENDER=251,
            load_transport=Mock(return_value=self.transport),
            target_current=lambda values, dual, single: dual if values[1:] == ['true', 'active', 'true', 'active'] else single)
        self.observations = supply.Observations()
        self.observations.powered_at = 0
        self.snapshot = self.enterContext(patch.object(supply, 'read_snapshot', return_value=snapshot()))
        self.ready = self.enterContext(patch.object(supply, 'native_ready', return_value=True))
        self.clock = self.enterContext(patch.object(supply.time, 'monotonic', return_value=6))
        self.enterContext(patch('builtins.open', mock_open()))
        self.lock = self.enterContext(patch.object(supply.fcntl, 'flock'))

    def test_first_snapshot_can_only_revoke_not_grant_pack_allowance(self):
        supply.renew(self.policy, self.observations)
        expression = self.transport.cmd_repl.call_args.args[1]
        self.assertIn('(boost-supply-lease 0 120.0)', expression)
        self.assertNotIn('conf-set', expression)
        self.connection.close.assert_called_once()

    def test_changed_two_pack_snapshot_renews_bounded_allowance(self):
        self.observations.observe(snapshot(), 5)
        self.snapshot.return_value = snapshot('100', '100')
        self.transport.cmd_repl.return_value = ['("BOOST-SUPPLY" 3 120.0f32)']
        supply.renew(self.policy, self.observations)
        self.assertIn('(boost-supply-lease 3 120.0)', self.transport.cmd_repl.call_args.args[1])

    def test_engine_off_and_boot_delay_produce_no_can_traffic(self):
        self.snapshot.return_value = ['off', *snapshot()[1:]]
        supply.renew(self.policy, self.observations)
        self.policy.load_transport.assert_not_called()
        self.snapshot.return_value = snapshot()
        supply.renew(self.policy, self.observations)
        self.policy.load_transport.assert_not_called()

    def test_missing_native_evidence_and_busy_lock_produce_no_can(self):
        self.ready.return_value = False
        supply.renew(self.policy, self.observations)
        self.policy.load_transport.assert_not_called()
        self.ready.return_value = True
        self.lock.side_effect = BlockingIOError()
        supply.renew(self.policy, self.observations)
        self.policy.load_transport.assert_not_called()

    def test_power_loss_during_identification_prevents_lease(self):
        self.snapshot.side_effect = [snapshot(), ['off', *snapshot()[1:]]]
        supply.renew(self.policy, self.observations)
        self.transport.cmd_repl.assert_not_called()
        self.connection.close.assert_called_once()

    def test_unsupported_controller_or_missing_ack_never_falls_back(self):
        self.transport.cmd_version.return_value['hw_name'] = 'other'
        with self.assertRaisesRegex(RuntimeError, 'unsupported controller'):
            supply.renew(self.policy, self.observations)
        self.transport.cmd_repl.assert_not_called()
        self.transport.cmd_version.return_value['hw_name'] = 'TUNU'
        self.transport.cmd_repl.return_value = ['BOOST_UNAVAILABLE']
        with self.assertRaisesRegex(RuntimeError, 'acknowledgement missing'):
            supply.renew(self.policy, self.observations)


if __name__ == '__main__':
    unittest.main()
