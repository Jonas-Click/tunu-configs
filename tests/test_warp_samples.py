#!/usr/bin/env python3
"""Exercise the resident sample player in a host LispBM REPL with mocked VESC I/O."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile

from test_drive_limits import forms

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = {"stop-music", "play-warp-samples-track"}
CONSTANTS = {"warp-sample-rate", "warp-sample-voltage"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repl", default=os.environ.get("LBM_REPL", "lbm"))
    parser.add_argument("--source", type=Path, default=ROOT / "app.lisp")
    args = parser.parse_args()
    selected = {}
    for form in forms(args.source.read_text()):
        match = re.match(r"\((?:def|defun)\s+([^\s()]+)", form)
        if match and match[1] in FUNCTIONS | CONSTANTS:
            if match[1] in selected:
                raise AssertionError("duplicate definition: " + match[1])
            selected[match[1]] = form
    if selected.keys() != FUNCTIONS | CONSTANTS:
        raise AssertionError("missing definitions: " + repr((FUNCTIONS | CONSTANTS) - selected.keys()))

    imports = re.findall(r"\(import \"samples/warp-sample-([0-5])\.s8\" 'warp-sample-\1\)",
                         args.source.read_text())
    if sorted(imports) != [str(i) for i in range(6)]:
        raise AssertionError("app must import all six signed sample chunks")

    prelude = r'''
(def calls 0)
(def stops 0)
(def statuses 0)
(def valid true)
(def cancel-after false)
(def channels (map (fn (x) (list x 0 0 0 0)) (range 8)))
(def music-master-vol 0.5)
(def current-song 7)
(def music-playing true)
(defun foc-play-samples (samples rate voltage) {
  (setq valid (and valid (= rate 16000.0) (= voltage 0.075)
                   (or (and (= calls 0) (= (buflen samples) 8002))
                       (and (= calls 1) (= (buflen samples) 8002))
                       (and (= calls 2) (= (buflen samples) 8002))
                       (and (= calls 3) (= (buflen samples) 8002))
                       (and (= calls 4) (= (buflen samples) 8002))
                       (and (= calls 5) (= (buflen samples) 4156)))))
  (if (and cancel-after (= calls 0)) (def current-song 8))
  (setq calls (+ calls 1))
  true
})
(defun foc-play-stop () { (setq stops (+ stops 1)) true })
(defun send-music-status () { (setq statuses (+ statuses 1)) true })
(def warp-sample-0 (array-create 8002))
(def warp-sample-1 (array-create 8002))
(def warp-sample-2 (array-create 8002))
(def warp-sample-3 (array-create 8002))
(def warp-sample-4 (array-create 8002))
(def warp-sample-5 (array-create 4156))
'''
    checks = r'''
(play-warp-samples-track 7)
(if (and (= calls 6) (= stops 1) (= statuses 1) valid
         (= current-song 0) (not music-playing))
    (print "WARP_SAMPLE_RUNTIME_PASS")
    (error "sample playback runtime assertion failed"))
(setq calls 0)
(setq stops 0)
(setq statuses 0)
(setq valid true)
(setq current-song 7)
(setq music-playing true)
(setq cancel-after true)
(play-warp-samples-track 7)
(if (and (= calls 1) (= stops 0) (= statuses 0) valid
         (= current-song 8) music-playing)
    (print "WARP_SAMPLE_CANCEL_PASS")
    (error "sample cancellation assertion failed"))
'''
    program = prelude + "\n" + "\n".join(selected[name] for name in sorted(selected)) + checks
    with tempfile.NamedTemporaryFile("w", suffix=".lbm") as source:
        source.write(program)
        source.flush()
        result = subprocess.run(
            [args.repl, "--silent", "--history_file=", "--heap_size=8192",
             "--memory_size=262144", "--src", source.name, "--terminate"],
            capture_output=True, text=True, timeout=15)
    output = result.stdout + result.stderr
    markers = ("WARP_SAMPLE_RUNTIME_PASS", "WARP_SAMPLE_CANCEL_PASS")
    if result.returncode or any(marker not in output for marker in markers):
        raise SystemExit("LispBM sample runtime failed (exit %d):\n%s" % (result.returncode, output))
    print("LispBM sample runtime passed: six chunk calls, frequency/voltage, stop/status, and cancellation mocks")


if __name__ == "__main__":
    main()
