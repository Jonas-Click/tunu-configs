# CAN Lisp uploads

`tunu-flash.py` uses Python 3's standard library and runs on the MDB. Confirm the vehicle is stationary and locked before powering the ECU. Do not upload while riding.

## Build with imports

Build offline before connecting to a scooter:

```sh
python3 tunu-flash.py build app.lisp --resource-root /path/to/resources -o app.img
```

The resource directory must contain the paths used by VESC Tool, such as `vesc_packages/lib_midi/midi.vescpkg`. Supported forms:

- `(import "file.bin" 'name)` — relative to the source file, or an absolute path.
- `(import "pkg::member@package.vescpkg" 'name)` — an embedded package member.
- `(import "pkg@package.vescpkg" 'name)` — the package's Lisp source.
- `://...` paths — resolved beneath `--resource-root`, not downloaded.

Each import needs a literal path and a unique quoted ASCII tag. Explicit local files can replace individual imports without editing the source:

```sh
python3 tunu-flash.py build app.lisp -o app.img \
  --import midi=/path/to/midi.bin \
  --import song1-track0=/path/to/axelf_melody.mid \
  --import song1-track1=/path/to/axelf_base.mid
```

Overrides take raw files, without the extra NUL added by the upload format. Use a native library built for the target firmware's C interface and architecture. Package assets are not bundled in this repository.

The format follows VESC Tool's [`CodeLoader::lispPackImports` and `lispUpload`](https://github.com/vedderb/vesc_tool/blob/5734bc01d53ce10567aa3a93059e717c9bd60954/codeloader.cpp): source/NUL, big-endian import count, name/NUL/offset/length entries, four-byte-aligned payloads, and a size/CRC/flags header. Each imported file has an extra terminating NUL. Package decoding supports the Qt-compressed VESC package format without requiring Qt.

Missing imports, malformed package tables, invalid CRCs and oversized STM32 images are rejected before changing engine power or erasing flash.

## Back up and upload

With the ECU already powered safely:

```sh
python3 tunu-flash.py --no-engine read --blob > before.blob
python3 tunu-flash.py build --blob before.blob -o rollback.img
python3 tunu-flash.py --no-engine write --image app.img --keep-output-disabled
python3 tunu-flash.py --no-engine stats
```

`read --blob` preserves the complete installed source/import blob; plain `read` outputs only source and is not a complete backup for an import-bearing program. A rollback image uploads with the same `write --image` command.

Upload inhibits throttle output for 120 seconds, stops Lisp, erases, writes, verifies the complete blob byte-for-byte and restarts Lisp. `--keep-output-disabled` leaves the remaining inhibition time for startup checks; it is not an indefinite lockout. Do not treat a start-command acknowledgement as proof that the program booted successfully. Inspect runtime state, faults and telemetry before returning the ECU to use. An `image-save` program also needs a cold-boot check.

Without `--no-engine`, CAN commands switch engine power on and, unless `--keep-engine` is supplied, off afterwards. This does not unlock the scooter, but powering the ECU still requires care around the throttle.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_flash_imports.py' -v
```
