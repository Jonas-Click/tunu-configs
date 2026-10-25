#!/usr/bin/env python3
"""Renew the native boost guard's bounded, observation-based pack allowance."""
from datetime import datetime
import fcntl
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import syslog
import time

FIELDS = ('present', 'state', 'voltage', 'current', 'serial-number',
          'fault-code', 'temperature-state', 'low-soc')
MAX_OBSERVATION_AGE = 2.0
POWER_ON_DELAY = 6.0
SNAPSHOT = """
local values = {redis.call('HGET', 'vehicle', 'engine-power') or ''}
for slot = 0, 1 do
  local key = 'battery:' .. slot
  for _, field in ipairs({'present','state','voltage','current','serial-number','fault-code','temperature-state','low-soc'}) do
    table.insert(values, redis.call('HGET', key, field) or '')
  end
end
return values
"""
ACK = re.compile(r'\("BOOST-SUPPLY" ([0-3]) ([-+0-9.eE]+)(?:f32)?\)')


class Observations:
    def __init__(self):
        self.slots = [None, None]
        self.powered_at = None

    def observe(self, values, now):
        if len(values) != 17:
            raise RuntimeError('invalid boost supply snapshot')
        mask = 0
        for slot in range(2):
            frame = tuple(values[1 + slot * 8:9 + slot * 8])
            old = self.slots[slot]
            changed_at = None
            if old and frame[4] == old[0][4]:
                changed_at = now if frame != old[0] else old[1]
            self.slots[slot] = (frame, changed_at)
            try:
                voltage, current = float(frame[2]), float(frame[3])
            except ValueError:
                continue
            eligible = (frame[0:2] == ('true', 'active') and bool(frame[4])
                        and frame[5:] == ('0', 'ideal', 'false')
                        and math.isfinite(voltage) and voltage > 0 and math.isfinite(current))
            if eligible and changed_at is not None and 0 <= now - changed_at < MAX_OBSERVATION_AGE:
                mask |= 1 << slot
        return mask


def load_policy_module():
    spec = importlib.util.spec_from_file_location('battery_current', Path(__file__).with_name('battery-current.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_snapshot():
    return subprocess.run(['redis-cli', '--raw', 'EVAL', SNAPSHOT, '0'], check=True,
                          capture_output=True, text=True, timeout=2).stdout.splitlines()


def native_ready():
    raw = subprocess.run(['redis-cli', '--raw', 'GET', 'engine-ecu:boost'], check=True,
                         capture_output=True, text=True, timeout=2).stdout.strip()
    if not raw or len(raw) > 2048:
        return False
    try:
        status = json.loads(raw)
        if (not isinstance(status, dict) or type(status.get('version')) is not int or status.get('version') != 1
                or status.get('valid') is not True or status.get('heartbeat_fresh') is not True):
            return False
        observed = datetime.fromisoformat(status['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None:
            return False
        return 0 <= time.time() - observed.timestamp() < 0.5
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def renew(policy, observations):
    _, dual, single, enabled = policy.load_boost_policy(Path(__file__).with_name('battery-current.json'))
    if not enabled:
        raise RuntimeError('boost_guard must be explicitly enabled in battery-current.json')
    values = read_snapshot()
    if len(values) != 17:
        raise RuntimeError('invalid boost supply snapshot')
    if values[0] != 'on':
        observations.slots = [None, None]
        observations.powered_at = None
        return
    now = time.monotonic()
    observations.observe(values, now)
    if observations.powered_at is None:
        observations.powered_at = now
    if now - observations.powered_at < POWER_ON_DELAY or not native_ready():
        return
    with open('/run/lock/tunu-battery-current.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        transport = policy.load_transport()
        connection = transport.VescCan('can0', 70, policy.SENDER, 1.0)
        try:
            identity = transport.cmd_version(connection)
            if (not identity or not identity.get('hw_name', '').startswith('TUNU')
                    or (identity['major'], identity['minor']) not in ((7, 0), (7, 1))):
                raise RuntimeError('unsupported controller for boost guard')
            values = read_snapshot()
            if len(values) != 17:
                raise RuntimeError('invalid boost supply snapshot')
            if values[0] != 'on':
                observations.slots = [None, None]
                observations.powered_at = None
                return
            packs = observations.observe(values, time.monotonic())
            normal_state = [values[0], values[1], values[2], values[9], values[10]]
            normal = policy.target_current(normal_state, dual, single)
            expression = ('(if (boost-supply-lease %d %s) '
                          '(print (list "BOOST-SUPPLY" %d (boost-normal-current))) '
                          '(print "BOOST_UNAVAILABLE"))') % (packs, float(normal), packs)
            lines = transport.cmd_repl(connection, expression, collect=0.4)
            for line in lines:
                match = ACK.fullmatch(line.strip())
                if match and int(match[1]) == packs and abs(float(match[2]) - normal) < .1:
                    return
            raise RuntimeError('native boost supply acknowledgement missing: ' + repr(lines))
        finally:
            connection.close()


def main():
    syslog.openlog('tunu-boost-supply', syslog.LOG_PID, syslog.LOG_DAEMON)
    policy = load_policy_module()
    observations = Observations()
    last_error = None
    last_log = 0.0
    while True:
        started = time.monotonic()
        try:
            renew(policy, observations)
            last_error = None
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            text = str(error)
            if text != last_error or started - last_log >= 30:
                syslog.syslog(syslog.LOG_WARNING, text)
                last_log = started
            last_error = text
        time.sleep(max(0.05, 1.0 - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
