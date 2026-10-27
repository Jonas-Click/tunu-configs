# Resident PCM samples

The default app does not import samples or reserve a song number. This optional overlay plays a 2.76-second signed 8-bit mono PCM stream at 16 kHz, using the existing VESC sample API and two reusable buffers. It contains no recording or native library binaries.

Prepare exactly 44160 sample bytes, split them into eleven 4000-byte files and one 160-byte file named `samples/warp-sample-0.s8` through `samples/warp-sample-11.s8`. The generated app uses song number 7; check the music catalogue before assigning it. The imported buffers include a trailing NUL, which is excluded from playback.

Generate an opt-in source file outside the repository:

```sh
python3 tools/pcm-samples/build.py --output /private/bundle/app.lisp
```

Build the image with the matching `tunu-flash.py`, using a fresh target backup for its own native MIDI imports and the twelve sample files as overrides. Validate the image and resources before installation. This overlay is based on the repository app; do not use it to replace a customized target app without separately preserving and reviewing that target's logic and resources. No controller operation or firmware replacement is performed by this tool.

Foreground MIDI or samples pause the engine sound. A generation token prevents old sample tasks from feeding or stopping a newer playback. A 20 ms guard and 100 ms maximum wake lateness bound refill scheduling; an over-late refill ends the current effect.

Run the synthetic tests without media:

```sh
python3 -m unittest discover -s tests -p 'test_pcm_builder.py' -v
python3 tests/test_pcm_samples.py
python3 tests/test_pcm_samples.py --source /private/bundle/app.lisp --repl /path/to/lispBM/repl/repl
```

The source-generation and tests do not include or distribute audio. Only use sample recordings you have permission to use.
