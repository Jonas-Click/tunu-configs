#!/usr/bin/env python3
"""Convert a supplied 2.760-second audio clip to signed 8-bit mono at 16 kHz."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SAMPLE_BYTES = 44160
SAMPLE_RATE = 16000
REPO = Path(__file__).resolve().parents[2]


def convert_audio(source, destination, expected_sha256=None, ffmpeg="ffmpeg"):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError("input audio and output sample must be different files")
    if any(path == REPO or REPO in path.parents for path in (source, destination)):
        raise ValueError("source audio and converted samples must stay outside the repository")
    executable = shutil.which(ffmpeg)
    if executable is None:
        raise FileNotFoundError("ffmpeg executable not found: " + ffmpeg)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix="." + destination.name + ".", suffix=".s8", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        subprocess.run([
            executable, "-nostdin", "-hide_banner", "-v", "error",
            "-i", str(source), "-map", "0:a:0", "-f", "s8",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-y", str(temporary),
        ], check=True, capture_output=True, text=True)
        sample = temporary.read_bytes()
        if len(sample) != SAMPLE_BYTES:
            raise ValueError("converted clip must contain exactly %d samples; got %d" %
                             (SAMPLE_BYTES, len(sample)))
        digest = hashlib.sha256(sample).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("converted sample SHA256 does not match the expected digest")
        os.replace(temporary, destination)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="private source audio file")
    parser.add_argument("--output", type=Path, required=True, help="private output signed-8 sample stream")
    parser.add_argument("--expected-sha256", help="optional digest to verify the converted stream")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable (default: PATH lookup)")
    args = parser.parse_args()
    digest = convert_audio(args.input, args.output, args.expected_sha256, args.ffmpeg)
    print("converted %d samples at %d Hz; sha256=%s" %
          (SAMPLE_BYTES, SAMPLE_RATE, digest))


if __name__ == "__main__":
    main()
