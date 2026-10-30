#!/usr/bin/env python3
"""Exercise TUNU 1.1 FET temperature telemetry in a host LispBM REPL."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from test_drive_limits import forms

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repl", default=os.environ.get("LBM_REPL", "lbm"))
    parser.add_argument("--source", type=Path, default=ROOT / "app.lisp")
    args = parser.parse_args()
    selected = [form for form in forms(args.source.read_text())
                if form.startswith("(defun send-status2 ")]
    assert len(selected) == 1
    program = selected[0] + r'''
(def dataArray_0x7E1 (array-create 6))
(def temperature 0)
(def fault 7)
(def sent-id 0)
(def failures 0)
(defun get-temp-fet () temperature)
(defun get-fault () fault)
(defun map-vesc-fault (code) code)
(defun can-send-sid (id data) (setq sent-id id))
(loopforeach measured '(-20.0 0.0 20.8 25.0 85.0 100.0 110.0) {
    (setq temperature measured)
    (send-status2)
    (if (not (and (= (bufget-i8 dataArray_0x7E1 0) (to-i measured))
                  (= (bufget-u8 dataArray_0x7E1 1) 0)
                  (= (bufget-u32 dataArray_0x7E1 2) fault)
                  (= sent-id 0x7E1))) {
        (setq failures (+ failures 1))
        (print (list "FAIL" measured dataArray_0x7E1))
    })
})
(if (= failures 0) (print "TEMPERATURE_TESTS_PASS") (print "TEMPERATURE_TESTS_FAIL"))
'''
    with tempfile.TemporaryDirectory(prefix="tunu-temperature-tests-") as directory:
        path = Path(directory) / "test.lisp"
        path.write_text(program)
        result = subprocess.run(
            [args.repl, "--silent", "--history_file", "", "--terminate",
             "--heap_size=131072", "--memory_size=4194304", "--src", str(path)],
            text=True, capture_output=True, timeout=30)
    print(result.stdout, end="")
    assert result.returncode == 0, result.stderr
    assert "TEMPERATURE_TESTS_PASS" in result.stdout and "TEMPERATURE_TESTS_FAIL" not in result.stdout
    print("PASS: 7 measured temperatures and CAN frame fields")


if __name__ == "__main__":
    main()
