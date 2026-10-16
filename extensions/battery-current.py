#!/usr/bin/env python3
"""Reconcile runtime TUNU input limits from the active battery count."""
import fcntl
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import syslog
import time

SENDER = 251
WAIT_SECONDS = 20.0
SNAPSHOT = """
local values = {redis.call('HGET', 'vehicle', 'engine-power') or ''}
for slot = 0, 1 do
  local key = 'battery:' .. slot
  table.insert(values, redis.call('HGET', key, 'present') or '')
  table.insert(values, redis.call('HGET', key, 'state') or '')
end
return values
"""
READBACK = '(print (list "TUNU-LIMITS" (conf-get \'l-in-current-max) (conf-get \'l-watt-max)))'
NUMBER = r'([-+]?[0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?)(?:f32)?'
RESULT = re.compile(r'\("TUNU-LIMITS"\s+' + NUMBER + r'\s+' + NUMBER + r'\)')


def report(message):
    # event-service discards successful exec stdout, so use the journal directly.
    syslog.syslog(syslog.LOG_INFO, message)
    print(message, file=sys.stderr, flush=True)


def read_state():
    result = subprocess.run(
        ['redis-cli', '--raw', 'EVAL', SNAPSHOT, '0'],
        check=True, capture_output=True, text=True, timeout=2,
    )
    values = result.stdout.splitlines()
    if len(values) != 5:
        raise RuntimeError('invalid battery snapshot: ' + result.stdout.strip())
    return values


def target_current(values):
    return 150 if values[1:] == ['true', 'active', 'true', 'active'] else 80


def load_transport():
    path = Path(__file__).with_name('tunu-flash.py')
    spec = importlib.util.spec_from_file_location('tunu_transport', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_power_limit(path):
    if not path.exists():
        return None
    settings = json.loads(path.read_text())
    if set(settings) - {'power_max_w'}:
        raise ValueError('unknown battery-current setting')
    watts = settings.get('power_max_w')
    if watts is not None and (isinstance(watts, bool) or not isinstance(watts, (int, float))
                              or not math.isfinite(watts) or watts <= 0):
        raise ValueError('power_max_w must be a positive finite number')
    return watts


def query_limits(transport, connection, expression, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError('controller readiness deadline expired')
    lines = transport.cmd_repl(connection, expression, collect=min(1.5, remaining))
    for line in lines:
        match = RESULT.fullmatch(line.strip())
        if match:
            return tuple(float(value) for value in match.groups())
    raise RuntimeError('limit readback missing: ' + repr(lines))


def matches(limits, amps, watts):
    return abs(limits[0] - amps) < 0.1 and (watts is None or abs(limits[1] - watts) < 0.1)


def reconcile(transport, watts=None, duration=WAIT_SECONDS):
    deadline = time.monotonic() + duration
    connection = None
    attempt = 0
    last_error = 'no attempt completed'
    try:
        while time.monotonic() < deadline:
            state = read_state()
            if state[0] != 'on':
                report('skipped: engine power off')
                return
            attempt += 1
            try:
                if connection is None:
                    connection = transport.VescCan('can0', 70, SENDER, 1.0)
                # Probe Lisp readiness before changing limits; power-on precedes autostart.
                limits = query_limits(transport, connection, READBACK, deadline)
                state = read_state()
                if state[0] != 'on':
                    report('skipped: engine power turned off')
                    return
                amps = target_current(state)
                if not matches(limits, amps, watts):
                    expression = "(progn (conf-set 'l-in-current-max %s) " % float(amps)
                    if watts is not None:
                        expression += "(conf-set 'l-watt-max %s) " % float(watts)
                    limits = query_limits(transport, connection, expression + READBACK + ')', deadline)
                    if not matches(limits, amps, watts):
                        raise RuntimeError('unexpected limit readback: ' + repr(limits))
                after = read_state()
                if after[0] != 'on':
                    report('skipped: engine power turned off after readback')
                    return
                if target_current(after) != amps:
                    report('battery state changed; reconciling again')
                    continue
                report('verified: battery=%gA power=%gW packs=%s/%s sender=%d attempt=%d'
                       % (limits[0], limits[1], after[2], after[4], SENDER, attempt))
                return
            except (OSError, RuntimeError) as error:
                last_error = str(error)
                report('attempt=%d pending: %s' % (attempt, last_error))
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        raise RuntimeError('limits not confirmed within %gs: %s' % (duration, last_error))
    finally:
        if connection is not None:
            connection.close()


def main():
    syslog.openlog('tunu-battery-current', syslog.LOG_PID, syslog.LOG_DAEMON)
    watts = load_power_limit(Path(__file__).with_suffix('.json'))
    with open('/run/lock/tunu-battery-current.lock', 'a') as lock:
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('battery-current reconciliation lock timed out')
                time.sleep(0.1)
        reconcile(load_transport(), watts, duration=max(0.0, deadline - time.monotonic()))


if __name__ == '__main__':
    main()
