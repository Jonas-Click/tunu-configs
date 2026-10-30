#!/usr/bin/env python3
"""Record the native guard binary and the reviewed VESC C interface."""
import argparse
import hashlib
import json
from pathlib import Path

SUPPORTED_INTERFACES = {
    "ecbf69f3c9df0063988c035adf2feac11667fd6df0f3018c5202851c1251fa2c": "7.00",
    "a458d0639460591c34d9eb14b5ba1c5a93659772e3320790449cf62fcae15937": "7.01",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    header = hashlib.sha256((args.sdk / "vesc_c_if.h").read_bytes()).hexdigest()
    if header not in SUPPORTED_INTERFACES:
        raise SystemExit("Unreviewed VESC C interface; verify its ABI before adding support")
    binary = args.binary.read_bytes()
    if len(binary) < 8 or len(binary) > 32768:
        raise SystemExit("Unexpected native guard image size")
    data = {"api": 1, "sdk_sha256": header, "sdk_version": SUPPORTED_INTERFACES[header],
            "sha256": hashlib.sha256(binary).hexdigest(), "bytes": len(binary)}
    args.binary.with_suffix(".json").write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
