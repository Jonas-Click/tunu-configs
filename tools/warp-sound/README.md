# Offline sampled warp-sound tooling

The repository contains reusable conversion and opt-in build tools, not a recording or captured VESC package binaries. Normal app builds and tests need none of the private inputs or FFmpeg. Keep supplied audio, converted samples, native resource backups, catalogues containing private entries, and generated bundles outside Git. No project-wide license or redistribution permission for a supplied recording is implied.

## Requirements

- Python 3 with its standard library.
- FFmpeg available on `PATH` for audio conversion.
- A local `tunu-configs` checkout and the matching `tunu-flash.py` image builder.
- For a complete image, a private resource-backup directory containing `installed-imports.json`, `installed-midi.bin`, `installed-song1-track0.bin`, and `installed-song1-track1.bin` from the target readback. These captured binaries are build inputs only and must not be copied into this repository.
- A base music-service `songs.json` to preserve.

## Reproduce a private build

Choose private paths for the audio file, resource backups, base catalogue, and output directory. The output directory must be outside this repository. Record `ffmpeg -version` alongside build results when exact reproduction matters.

Convert a supplied audio file to raw signed 8-bit, mono, 16 kHz PCM:

```sh
python3 tools/warp-sound/convert_audio.py \
  --input "$SOURCE_AUDIO" \
  --output "$PRIVATE_OUTPUT/warp-sound-16000.s8" \
  --expected-sha256 "$EXPECTED_SAMPLE_SHA256"
```

`--expected-sha256` is optional when first producing a new sample; the command prints the resulting digest. The fixed player format requires exactly 44,160 samples (2.760 seconds). Conversion uses FFmpeg's first audio stream, `-f s8 -ac 1 -ar 16000`, with no normalization filter. Supply a clip of the intended duration; the converter rejects other output lengths and does not truncate or pad.

Build the complete opt-in app and staged catalogue from that converted stream:

```sh
python3 tools/warp-sound/build_private.py \
  --sample-stream "$PRIVATE_OUTPUT/warp-sound-16000.s8" \
  --sample-sha256 "$EXPECTED_SAMPLE_SHA256" \
  --resource-backup "$RESOURCE_BACKUP" \
  --base-catalog "$BASE_CATALOG" \
  --output-dir "$PRIVATE_OUTPUT"
```

Alternatively, use `--source-audio "$SOURCE_AUDIO"` instead of `--sample-stream` to convert during the build. The expected digest is optional for the builder; when omitted, it prints the converted sample digest. `--sample-sha256` verifies a previously recorded digest.

The builder rejects a catalogue where song 7 or `TNG Warp 2` already exists. It preserves existing entries, injects private sample imports/player/dispatch only into the generated app, separately builds the generic app without sample files, validates preserved resources against readback metadata, checks image imports including their NUL terminators, and enforces VESC image capacity. It writes the preview, sample chunks, app image/source, staged catalogue, and disabled event extension only to the selected private output directory. It never writes to a vehicle.

## Offline checks

```sh
python3 tests/test_warp_audio.py
python3 tests/test_warp_private_builder.py
python3 tests/test_warp_samples.py
python3 tests/test_warp_samples.py --source "$PRIVATE_OUTPUT/bundle/vesc/app.lisp" --repl "$LBM_REPL"
python3 tests/test_warp_sound.py
```

The converter tests do not need FFmpeg or private media. The private LispBM test uses mocked VESC calls to exercise generation identity, replay/stop/switch races, bounded two-buffer scheduling, late-refill abort, and complete final-sample playback.

A successful offline build does not verify target firmware/API compatibility, the installed music-service binary, event reconnect continuity, playback safety, or redistribution rights. Keep the trigger disabled and do not install or play the image without separate review and approval.
