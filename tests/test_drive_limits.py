#!/usr/bin/env python3
"""Run app.lisp current-limit functions in a host LispBM REPL with mocked IO."""
import argparse
import itertools
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def forms(source):
    """Extract balanced top-level forms without evaluating hardware startup."""
    stack = []
    start = None
    string = escape = comment = False
    for i, char in enumerate(source):
        if comment:
            comment = char != "\n"
        elif string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                string = False
        elif char == ";":
            comment = True
        elif char == '"':
            string = True
        elif char in "({[":
            if not stack:
                start = i
            stack.append(char)
        elif char in ")}]":
            assert stack and stack.pop() == {")": "(", "}": "{", "]": "["}[char]
            if not stack:
                yield source[start:i + 1]
    assert not stack and not string, "Unbalanced source"


def brake_scale(rpm, enabled):
    rpm = abs(rpm)
    if rpm < 300:
        return 0.0
    if not enabled:
        return 1.0
    return 0.25 + 0.75 * min(1.0, max(0.0, (rpm - 2200) / 400))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repl", default=os.environ.get("LBM_REPL", "lbm"))
    parser.add_argument("--source", type=Path, default=ROOT / "app.lisp")
    args = parser.parse_args()
    for filename in ("vesc_appconf.xml", "3kw-vesc_appconf.xml"):
        config = ET.parse(ROOT / filename).getroot()
        assert config.findtext("app_adc_conf.ctrl_type") == "8"
        assert float(config.findtext("app_adc_conf.hyst")) == 0.15
        assert config.findtext("app_adc_conf.buttons") == "0"
    names = {
        "kers-enabled", "kers-current", "kers-voltage", "kers-voltage-taper",
        "kers-amps-now", "kers-fade-start-erpm", "kers-fade-end-erpm",
        "kers-floor", "kers-hold-cutoff-erpm", "kers-drive-request-level",
        "kers-drive-regen-floor", "kers-scale-slew", "kers-scale",
        "drive-requested", "kers-fade-target", "update-kers-fade",
        "kers-voltage-scale", "update-kers-current",
    }
    selected = {}
    for form in forms(args.source.read_text()):
        match = re.match(r"\((?:def|defun)\s+([^\s()]+)", form)
        if match and match[1] in names:
            assert match[1] not in selected, f"Duplicate definition: {match[1]}"
            selected[match[1]] = form
    assert selected.keys() == names, names - selected.keys()

    cases = []
    speeds = [-6000, -2601, -2600, -2400, -2200, -301, -300, -299, 0,
              299, 300, 301, 2200, 2400, 2600, 2601, 6000]
    inputs = [(0, 0), (0, 1), (0.15, 0), (0.16, 0), (0.8, 0.9), (0.8, 0.2)]
    for rpm, enabled, (throttle, brake), amps, voltage in itertools.product(
            speeds, [False, True], inputs, [0, 1000, 10000], [54, 56, 57.5, 59, 60]):
        driving = throttle - brake > 0.15
        motor = 1.0 if driving else brake_scale(rpm, enabled)
        taper = min(1.0, max(0.0, (59 - voltage) / 3))
        charge = min(-amps / 1000 if enabled else 0, -3 if driving else 0) * taper
        released_motor = brake_scale(rpm, enabled)
        released_charge = (-amps / 1000 if enabled else 0) * taper
        cases.append(f"({rpm} {'t' if enabled else 'nil'} {throttle} {brake} {amps} {voltage} "
                     f"{motor} {charge} {released_motor} {released_charge} {'t' if driving else 'nil'})")

    program = "\n".join(selected.values()) + r'''
(def throttle 0)
(def brake 0)
(def rpm 0)
(def vin 54)
(def motor-limit 0)
(def input-limit 0)
(def failed 0)
(def checked 0)
(defun get-adc-decoded (channel) (if (= channel 0) throttle brake))
(defun get-rpm () rpm)
(defun get-vin () vin)
(defun conf-set (key value)
    (if (eq key 'l-current-min-scale)
        (setq motor-limit value)
        (if (eq key 'l-in-current-min)
            (setq input-limit value)
            (setq failed (+ failed 1)))))
(defun expect (actual expected label) {
    (setq checked (+ checked 1))
    (if (> (abs (- actual expected)) 0.0001) {
        (setq failed (+ failed 1))
        (print (list "FAIL" label rpm throttle brake kers-enabled vin actual expected))
    })
})
'''
    program += "\n(loopforeach case '(\n" + "\n".join(cases) + r'''
) {
    (setq rpm (ix case 0))
    (setq kers-enabled (ix case 1))
    (setq throttle (ix case 2))
    (setq brake (ix case 3))
    (setq kers-current (ix case 4))
    (setq vin (ix case 5))
    (setq kers-scale 0.0)
    (setq kers-amps-now 0.0)
    (setq motor-limit 0.0)
    (setq input-limit 0.0)
    (expect (kers-fade-target (abs rpm)) (ix case 6) "motor target")
    (update-kers-fade)
    (update-kers-current)
    (expect motor-limit (if (ix case 10) 1.0 (if (> (ix case 6) 0.08) 0.08 (ix case 6))) "first tick")
    (expect input-limit (ix case 7) "charging allowance")
    (looprange i 0 14 (update-kers-fade))
    (expect motor-limit (ix case 6) "settled motor limit")
    (setq throttle 0.0)
    (setq brake 1.0)
    (var previous motor-limit)
    (update-kers-fade)
    (expect (abs (- motor-limit previous))
            (if (> (abs (- (ix case 8) previous)) 0.08) 0.08 (abs (- (ix case 8) previous)))
            "brake transition slew")
    (looprange i 0 14 (update-kers-fade))
    (update-kers-current)
    (expect motor-limit (ix case 8) "brake motor limit")
    (expect input-limit (ix case 9) "brake charging allowance")
})
(print (list "checks" checked "failures" failed))
(if (= failed 0) (print "DRIVE_LIMIT_TESTS_PASS") (print "DRIVE_LIMIT_TESTS_FAIL"))
'''
    with tempfile.TemporaryDirectory(prefix="tunu-drive-tests-") as directory:
        path = Path(directory) / "test.lisp"
        path.write_text(program)
        result = subprocess.run(
            [args.repl, "--silent", "--history_file", "", "--terminate",
             "--heap_size=131072", "--memory_size=4194304", "--src", str(path)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
        )
    print(result.stdout)
    assert result.returncode == 0 and "DRIVE_LIMIT_TESTS_PASS" in result.stdout, "LispBM checks failed"
    print(f"PASS: {len(cases)} cases using {args.source}")


if __name__ == "__main__":
    main()
