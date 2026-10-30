#!/usr/bin/env python3
"""Build an opt-in boost app using the target's own native MIDI resources."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

REPO = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO / "app.lisp")
    parser.add_argument("--native-bin", type=Path, required=True)
    parser.add_argument("--resource-backup", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--enable-policy", action="store_true")
    args = parser.parse_args()
    common = load(REPO / "tools/warp-sound/build_private.py", "private_builder")
    flash = load(REPO / "tunu-flash.py", "tunu_flash")
    manifest = load(Path(__file__).with_name("manifest.py"), "boost_manifest")
    output = common.prepare_private_output(args.output_dir)
    native = args.native_bin.read_bytes()
    metadata = json.loads(args.native_bin.with_suffix(".json").read_text())
    if (not 8 <= len(native) <= 32768 or metadata.get("api") != 1
            or metadata.get("sdk_sha256") not in manifest.SUPPORTED_INTERFACES
            or metadata.get("bytes") != len(native)
            or metadata.get("sha256") != hashlib.sha256(native).hexdigest()):
        raise ValueError("native guard metadata or binary mismatch")
    source = args.source.read_text()
    replacements = {
        "; @boost-native-imports": '(import "boost_guard.bin" \'boost-guard-lib)',
        "(def boost-native-enabled false)": "(def boost-native-enabled true)",
    }
    if args.enable_policy:
        replacements["(def boost-policy-enabled false)"] = "(def boost-policy-enabled true)"
    for before, after in replacements.items():
        if source.count(before) != 1:
            raise ValueError("expected one boost injection point: " + before)
        source = source.replace(before, after)
    overrides = common.stock_resource_overrides(args.resource_backup.resolve())
    overrides["boost-guard-lib"] = args.native_bin.resolve()
    image, _ = flash.build_image(source.encode(), base_dir=args.source.resolve().parent, overrides=overrides)
    blob = flash.validate_image(image)
    packed_source, imports = flash.unpack_blob(blob)
    if packed_source != source.encode() or imports.get("boost-guard-lib") != native + b"\0":
        raise ValueError("boost image readback mismatch")
    for tag, path in overrides.items():
        if imports.get(tag) != path.read_bytes() + b"\0":
            raise ValueError("native resource differs: " + tag)
    (output / "app.lisp").write_text(source)
    (output / "boost.img").write_bytes(image)
    shutil.copyfile(args.native_bin, output / "boost_guard.bin")
    (output / "build.json").write_text(json.dumps({
        "image_sha256": hashlib.sha256(image).hexdigest(), "image_bytes": len(image),
        "native": metadata, "policy_enabled": "(def boost-policy-enabled true)" in source,
        "imports": sorted(imports),
    }, indent=2) + "\n")
    print("image: %s (%d/%d bytes)" % (output / "boost.img", len(image), flash.MAX_IMAGE_SIZE))
    print("policy enabled: %s" % ("(def boost-policy-enabled true)" in source))


if __name__ == "__main__":
    main()
