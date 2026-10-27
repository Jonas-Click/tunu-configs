#!/usr/bin/env python3
"""Generate an opt-in 16 kHz PCM overlay without embedding any audio."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMPORTS = '(import "samples/warp-sample-0.s8" \'warp-sample-0)\n(import "samples/warp-sample-1.s8" \'warp-sample-1)\n(import "samples/warp-sample-2.s8" \'warp-sample-2)\n(import "samples/warp-sample-3.s8" \'warp-sample-3)\n(import "samples/warp-sample-4.s8" \'warp-sample-4)\n(import "samples/warp-sample-5.s8" \'warp-sample-5)\n(import "samples/warp-sample-6.s8" \'warp-sample-6)\n(import "samples/warp-sample-7.s8" \'warp-sample-7)\n(import "samples/warp-sample-8.s8" \'warp-sample-8)\n(import "samples/warp-sample-9.s8" \'warp-sample-9)\n(import "samples/warp-sample-10.s8" \'warp-sample-10)\n(import "samples/warp-sample-11.s8" \'warp-sample-11)'
CONFIG = '(def warp-samples-enabled true)\n(def warp-song-number 7)\n(def warp-sample-rate 16000.0)\n(def warp-sample-voltage 0.15)\n(def warp-sample-guard 0.02)\n(def warp-max-scheduler-lateness 0.10)'
INIT = '(def warp-play-buffer-a (array-create 4000))\n    (def warp-play-buffer-b (array-create 4000))\n    (def warp-play-buffer-tail (array-create 160))\n    (def warp-sample-chunks (list warp-sample-0 warp-sample-1 warp-sample-2\n                                  warp-sample-3 warp-sample-4 warp-sample-5\n                                  warp-sample-6 warp-sample-7 warp-sample-8\n                                  warp-sample-9 warp-sample-10 warp-sample-11))'
DISPATCH = '((and warp-samples-enabled (= cmd warp-song-number)) (play-warp-samples))'
FUNCTIONS = '(defun warp-generation-current (generation)\n    (atomic (= generation music-play-generation))\n)\n\n(defun warp-queue-initial (generation) {\n    (atomic (if (= generation music-play-generation) {\n        (var first-count (- (buflen warp-sample-0) 1))\n        (var second-count (- (buflen warp-sample-1) 1))\n        (bufcpy warp-play-buffer-a 0 warp-sample-0 0 first-count)\n        (foc-play-samples warp-play-buffer-a warp-sample-rate\n                          (* warp-sample-voltage music-master-vol))\n        (bufcpy warp-play-buffer-b 0 warp-sample-1 0 second-count)\n        (foc-play-samples warp-play-buffer-b warp-sample-rate\n                          (* warp-sample-voltage music-master-vol))\n        true\n    } false))\n})\n\n(defun warp-queue-chunk (generation destination source sample-count) {\n    (atomic (if (= generation music-play-generation) {\n        (bufcpy destination 0 source 0 sample-count)\n        (foc-play-samples destination warp-sample-rate\n                          (* warp-sample-voltage music-master-vol))\n        true\n    } false))\n})\n\n; Two slots are prefilled; 20ms guard and 100ms late limit preserve lookahead.\n(defun warp-wait-until (generation start-time deadline) {\n    (var remaining (- deadline (secs-since start-time)))\n    (if (and (> remaining 0.0) (warp-generation-current generation))\n        (sleep remaining))\n    (and (warp-generation-current generation)\n         (<= (- (secs-since start-time) deadline) warp-max-scheduler-lateness))\n})\n\n(defun warp-finish-if-current (generation) {\n    (atomic (if (= generation music-play-generation) {\n        (def music-play-generation (+ music-play-generation 1))\n        (def current-song 0)\n        (def music-active-tracks 0)\n        (def music-starting false)\n        (foc-play-stop)\n        (loopforeach ch channels { (setix ch 1 0) })\n        (def music-playing false)\n        (send-music-status)\n        true\n    } false))\n})\n\n(defun play-warp-samples-track (generation) {\n    (var start-time (systime))\n    (var queued-samples (+ (- (buflen warp-sample-0) 1)\n                           (- (buflen warp-sample-1) 1)))\n    (var previous-count (- (buflen warp-sample-1) 1))\n    (var sample-index 0)\n    (var sequence-ok (warp-queue-initial generation))\n    (loopforeach source warp-sample-chunks {\n        (if (>= sample-index 2) {\n            (var sample-count (- (buflen source) 1))\n            (var deadline (+ (/ (- queued-samples previous-count) warp-sample-rate)\n                             warp-sample-guard))\n            (if (not (warp-wait-until generation start-time deadline))\n                (setq sequence-ok false))\n            (if (and sequence-ok (warp-generation-current generation)) {\n                (var destination (if (= sample-index 11) warp-play-buffer-tail\n                                     (if (= (mod sample-index 2) 0)\n                                         warp-play-buffer-a warp-play-buffer-b)))\n                (if (warp-queue-chunk generation destination source sample-count) {\n                    (setq queued-samples (+ queued-samples sample-count))\n                    (setq previous-count sample-count)\n                } {\n                    (setq sequence-ok false)\n                })\n            } {\n                (setq sequence-ok false)\n            })\n        })\n        (setq sample-index (+ sample-index 1))\n    })\n    (if (and sequence-ok (= sample-index 12))\n        (warp-wait-until generation start-time\n                         (+ (/ queued-samples warp-sample-rate) warp-sample-guard)))\n    (warp-finish-if-current generation)\n})\n\n(defun play-warp-samples () {\n    (var generation 0)\n    (atomic {\n        (def music-play-generation (+ music-play-generation 1))\n        (setq generation music-play-generation)\n        (def music-starting true)\n        (def current-song 0)\n        (def music-playing false)\n        (def music-active-tracks 0)\n        (engine-pause)\n        (foc-play-stop)\n        (loopforeach ch channels { (setix ch 1 0) })\n    })\n    (sleep 0.05)\n    (atomic (if (= generation music-play-generation) {\n        (def music-t-start (systime))\n        (def current-song warp-song-number)\n        (def music-playing true)\n        (def music-starting false)\n        (spawn 200 play-warp-samples-track generation)\n    }))\n})'


def injected_app(source):
    markers = {
        "; @pcm-sample-imports": IMPORTS,
        "; @pcm-sample-config": CONFIG,
        "; @pcm-sample-functions": FUNCTIONS,
        "; @pcm-sample-init": INIT,
        "; @pcm-sample-dispatch": DISPATCH,
    }
    for marker, insertion in markers.items():
        if source.count(marker) != 1:
            raise ValueError("expected exactly one PCM insertion point: " + marker)
        source = source.replace(marker, insertion)
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("generated app must be outside the repository")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(injected_app((ROOT / "app.lisp").read_text()))
    output.chmod(0o600)


if __name__ == "__main__":
    main()
