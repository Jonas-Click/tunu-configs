#!/usr/bin/env python3
"""Build an opt-in, sample-enabled app and staged music catalogue offline."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import wave

REPO = Path(__file__).resolve().parents[2]
SONG_ID = 7
ENTRY = {"number": SONG_ID, "name": "TNG Warp 2", "source": "builtin-samples"}
SAMPLE_BYTES = 44160
CHUNK_BYTES = 4000
RESOURCE_FILES = {
    "midi": "installed-midi.bin",
    "song1-track0": "installed-song1-track0.bin",
    "song1-track1": "installed-song1-track1.bin",
}

IMPORTS = "\n".join(
    '(import "samples/warp-sample-%d.s8" \'warp-sample-%d)' % (i, i)
    for i in range(12)
)
CONFIG = """(def music-play-generation 0)
(def warp-samples-enabled true)
(def warp-song-number %d)
(def warp-sample-rate 16000.0)
(def warp-sample-voltage 0.15)
(def warp-sample-guard 0.02)
(def warp-max-scheduler-lateness 0.10)""" % SONG_ID
INIT = """(def warp-play-buffer-a (array-create 4000))
    (def warp-play-buffer-b (array-create 4000))
    (def warp-play-buffer-tail (array-create 160))
    (def warp-sample-chunks (list warp-sample-0 warp-sample-1 warp-sample-2
                                  warp-sample-3 warp-sample-4 warp-sample-5
                                  warp-sample-6 warp-sample-7 warp-sample-8
                                  warp-sample-9 warp-sample-10 warp-sample-11))"""
DISPATCH = "((and warp-samples-enabled (= cmd warp-song-number)) (play-warp-samples))"

WARP_FUNCTIONS = r'''(defun advance-music-generation () {
    (atomic {
        (def music-play-generation (+ music-play-generation 1))
        music-play-generation
    })
})

(defun warp-generation-current (generation)
    (atomic (= generation music-play-generation))
)

(defun warp-queue-initial (generation) {
    (atomic (if (= generation music-play-generation) {
        (var first-count (- (buflen warp-sample-0) 1))
        (var second-count (- (buflen warp-sample-1) 1))
        (bufcpy warp-play-buffer-a 0 warp-sample-0 0 first-count)
        (foc-play-samples warp-play-buffer-a warp-sample-rate
                          (* warp-sample-voltage music-master-vol))
        (bufcpy warp-play-buffer-b 0 warp-sample-1 0 second-count)
        (foc-play-samples warp-play-buffer-b warp-sample-rate
                          (* warp-sample-voltage music-master-vol))
        true
    } false))
})

(defun warp-queue-chunk (generation destination source sample-count) {
    (atomic (if (= generation music-play-generation) {
        (bufcpy destination 0 source 0 sample-count)
        (foc-play-samples destination warp-sample-rate
                          (* warp-sample-voltage music-master-vol))
        true
    } false))
})

; Two slots are prefilled; 20ms guard and 100ms late limit preserve lookahead.
(defun warp-wait-until (generation start-time deadline) {
    (var remaining (- deadline (secs-since start-time)))
    (if (and (> remaining 0.0) (warp-generation-current generation))
        (sleep remaining))
    (and (warp-generation-current generation)
         (<= (- (secs-since start-time) deadline) warp-max-scheduler-lateness))
})

(defun warp-finish-if-current (generation) {
    (atomic (if (= generation music-play-generation) {
        (def music-play-generation (+ music-play-generation 1))
        (def current-song 0)
        (foc-play-stop)
        (loopforeach ch channels { (setix ch 1 0) })
        (def music-playing false)
        (send-music-status)
        true
    } false))
})

(defun play-warp-samples-track (generation) {
    (var start-time (systime))
    (var queued-samples (+ (- (buflen warp-sample-0) 1)
                           (- (buflen warp-sample-1) 1)))
    (var previous-count (- (buflen warp-sample-1) 1))
    (var sample-index 0)
    (var sequence-ok (warp-queue-initial generation))
    (loopforeach source warp-sample-chunks {
        (if (>= sample-index 2) {
            (var sample-count (- (buflen source) 1))
            (var deadline (+ (/ (- queued-samples previous-count) warp-sample-rate)
                             warp-sample-guard))
            (if (not (warp-wait-until generation start-time deadline))
                (setq sequence-ok false))
            (if (and sequence-ok (warp-generation-current generation)) {
                (var destination (if (= sample-index 11) warp-play-buffer-tail
                                     (if (= (mod sample-index 2) 0)
                                         warp-play-buffer-a warp-play-buffer-b)))
                (if (warp-queue-chunk generation destination source sample-count) {
                    (setq queued-samples (+ queued-samples sample-count))
                    (setq previous-count sample-count)
                } {
                    (setq sequence-ok false)
                })
            } {
                (setq sequence-ok false)
            })
        })
        (setq sample-index (+ sample-index 1))
    })
    (if (and sequence-ok (= sample-index 12)
             (warp-wait-until generation start-time
                              (+ (/ queued-samples warp-sample-rate)
                                 warp-sample-guard))) {
        (warp-finish-if-current generation)
    } {
        ; A late refill or cancellation must not let this task stop a newer play.
        (warp-finish-if-current generation)
    })
})

(defun play-warp-samples () {
    (var generation (advance-music-generation))
    (def current-song 0)
    (sleep 0.05)
    (atomic (if (= generation music-play-generation) {
        (foc-play-stop)
        (loopforeach ch channels { (setix ch 1 0) })
        (def music-t-start (systime))
        (def current-song warp-song-number)
        (def music-playing true)
        (spawn 200 play-warp-samples-track generation)
    }))
})'''

PRIVATE_FUNCTIONS = {
    "stop-music": '''(defun stop-music () {
    (var generation (advance-music-generation))
    (def current-song 0)
    (sleep 0.05)
    (atomic (if (= generation music-play-generation) {
        (foc-play-stop)
        (loopforeach ch channels { (setix ch 1 0) })
        (def music-playing false)
    }))
})''',
    "play-current-song": '''(defun play-current-song () {
    (var generation (advance-music-generation))
    (def current-song 0)
    (sleep 0.05)
    (if (= generation music-play-generation) {
        (if (or song-buf-0-ready song-buf-1-ready) {
            (foc-play-stop)
            (if song-buf-0-ready (ext-midi-open 0 song-buf-0))
            (if song-buf-1-ready (ext-midi-open 1 song-buf-1))
            (def music-t-start (systime))
            (def current-song resident-song-number)
            (if song-buf-0-ready (spawn 200 play-midi-track 0 song-buf-0-vol song-buf-0-decay resident-song-number))
            (if song-buf-1-ready (spawn 200 play-midi-track 1 song-buf-1-vol song-buf-1-decay resident-song-number))
            (def music-playing true)
        } {
            (print "No song buffer ready to play")
        })
    })
})''',
    "play-axelf": '''(defun play-axelf () {
    (var generation (advance-music-generation))
    (def current-song 0)
    (sleep 0.05)
    (if (= generation music-play-generation) {
        (foc-play-stop)
        (ext-midi-open 0 song1-track0)
        (ext-midi-open 1 song1-track1)
        (def music-t-start (systime))
        (def current-song axelf-song-number)
        (spawn 200 play-midi-track 0 2 0.92 axelf-song-number)
        (spawn 200 play-midi-track 1 1.0 0.92 axelf-song-number)
        (def music-playing true)
    })
})''',
}

MARKERS = {
    "; @private-warp-imports": IMPORTS,
    "; @private-warp-config": CONFIG,
    "; @private-warp-functions": WARP_FUNCTIONS,
    "; @private-warp-init": INIT,
    "; @private-warp-dispatch": DISPATCH,
}


def replace_defun(source, name, replacement):
    prefix = "(defun " + name
    start = source.find(prefix)
    if start < 0 or source.find(prefix, start + len(prefix)) >= 0:
        raise ValueError("expected one definition of " + name)
    depth = 0
    in_string = escaped = in_comment = False
    for offset in range(start, len(source)):
        char = source[offset]
        if in_comment:
            if char == "\n":
                in_comment = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == ";":
            in_comment = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[:start] + replacement + source[offset + 1:]
    raise ValueError("unterminated definition of " + name)


def injected_app(source):
    for marker, snippet in MARKERS.items():
        if source.count(marker) != 1:
            raise ValueError("expected exactly one app injection point: " + marker)
        source = source.replace(marker, snippet)
    for name, snippet in PRIVATE_FUNCTIONS.items():
        source = replace_defun(source, name, snippet)
    return source


def load_flash_module():
    spec = importlib.util.spec_from_file_location("tunu_flash", REPO / "tunu-flash.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stock_resource_overrides(backup):
    metadata = json.loads((backup / "installed-imports.json").read_text())
    by_tag = {item["tag"]: item for item in metadata}
    overrides = {}
    for tag, filename in RESOURCE_FILES.items():
        path = backup / filename
        data = path.read_bytes()
        entry = by_tag.get(tag)
        if entry is None or entry["length"] != len(data) + 1:
            raise ValueError("preserved resource length mismatch: " + tag)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("preserved resource hash mismatch: " + tag)
        overrides[tag] = path
    return overrides


def verify_image(flash, image, source, expected_imports):
    blob = flash.validate_image(image)
    packed_source, imports = flash.unpack_blob(blob)
    if packed_source != source:
        raise ValueError("packed source differs from generated application")
    if imports.keys() != expected_imports.keys():
        raise ValueError("unexpected import table tags")
    for tag, data in expected_imports.items():
        if imports[tag] != data + b"\x00":
            raise ValueError("imported resource mismatch: " + tag)
    return len(image)


def build_image(flash, source, base_dir, stock_overrides, samples=None):
    overrides = dict(stock_overrides)
    expected = {tag: path.read_bytes() for tag, path in stock_overrides.items()}
    if samples:
        for index, path in enumerate(samples):
            tag = "warp-sample-%d" % index
            overrides[tag] = path
            expected[tag] = path.read_bytes()
    image, _ = flash.build_image(source, base_dir=base_dir, overrides=overrides)
    verify_image(flash, image, source, expected)
    return image


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True, help="private signed 8-bit sample")
    parser.add_argument("--sample-sha256", required=True, help="expected sample-stream SHA256")
    parser.add_argument("--resource-backup", type=Path, required=True,
                        help="directory with installed-imports.json and preserved package resources")
    parser.add_argument("--base-catalog", type=Path, required=True,
                        help="existing songs.json to preserve and append song 7 to")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="private output directory outside the repository")
    return parser.parse_args()


def main():
    args = parse_args()
    sample = args.sample.read_bytes()
    if len(sample) != SAMPLE_BYTES or hashlib.sha256(sample).hexdigest() != args.sample_sha256:
        raise ValueError("sample does not match the expected signed 16 kHz mono stream")
    output = args.output_dir.resolve()
    if output == REPO or REPO in output.parents:
        raise ValueError("private generated bundle must be outside the repository")
    base_catalog = json.loads(args.base_catalog.read_text())
    if not isinstance(base_catalog, list) or any(not isinstance(item, dict) for item in base_catalog):
        raise ValueError("base catalogue must be an array of song objects")
    ids = [item.get("number") for item in base_catalog]
    if SONG_ID in ids:
        raise ValueError("song number %d already exists in the base catalogue" % SONG_ID)
    if ENTRY["name"] in [item.get("name") for item in base_catalog]:
        raise ValueError("song name already exists in the base catalogue")

    output.mkdir(parents=True, exist_ok=True)
    sample_dir = output / "bundle/vesc/samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index, offset in enumerate(range(0, len(sample), CHUNK_BYTES)):
        path = sample_dir / ("warp-sample-%d.s8" % index)
        path.write_bytes(sample[offset:offset + CHUNK_BYTES])
        chunks.append(path)
    if len(chunks) != 12 or b"".join(path.read_bytes() for path in chunks) != sample:
        raise ValueError("sample chunks do not reconstruct the source stream")

    preview = output / "tng-warp-2-sampled-preview.wav"
    pcm = b"".join(struct.pack("<h", (value if value < 128 else value - 256) * 256)
                   for value in sample)
    with wave.open(str(preview), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(pcm)

    source_path = REPO / "app.lisp"
    default_source = source_path.read_text()
    if "warp-samples-enabled" in default_source or "warp-song-number" in default_source:
        raise ValueError("generic application must not define private playback configuration")
    if "samples/warp-sample-" in default_source or "@private-warp-dispatch" not in default_source:
        raise ValueError("generic application unexpectedly depends on private sample assets")

    flash = load_flash_module()
    stock = stock_resource_overrides(args.resource_backup.resolve())
    default_image = build_image(flash, default_source.encode(), REPO, stock)
    private_source = injected_app(default_source)
    private_app = output / "bundle/vesc/app.lisp"
    private_app.write_text(private_source)
    image = build_image(flash, private_source.encode(), private_app.parent, stock, chunks)
    image_path = output / "bundle/vesc/warp-sample.img"
    image_path.write_bytes(image)

    catalog = list(base_catalog)
    catalog.append(ENTRY)
    stage = output / "bundle/music-service"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "songs.json").write_text(json.dumps(catalog, indent=2) + "\n")
    extension_dir = output / "bundle/extensions"
    extension_dir.mkdir(parents=True, exist_ok=True)
    for name in ("warp-sound.py", "warp-sound.toml"):
        shutil.copy2(REPO / "extensions" / name, extension_dir / name)
    limit = flash.MAX_IMAGE_SIZE
    if len(image) > limit:
        raise ValueError("private image exceeds VESC image capacity")
    print("default app image: %d bytes; private sample app image: %d/%d bytes" %
          (len(default_image), len(image), limit))
    print("staged sample song %d; preserved %d existing catalogue entries" %
          (SONG_ID, len(catalog) - 1))
    print("private image: %s sha256=%s" % (image_path, hashlib.sha256(image).hexdigest()))
    print("preview: %s" % preview)


if __name__ == "__main__":
    main()
