# Lisp policy and telemetry tests

Run with Python 3 and a host LispBM REPL:

```sh
python3 tests/test_drive_limits.py --repl /path/to/lispBM/repl/repl
python3 tests/test_temperature.py --repl /path/to/lispBM/repl/repl
python3 tests/test_warp_audio.py
python3 tests/test_warp_private_builder.py
python3 tests/test_warp_samples.py
python3 tests/test_warp_samples.py --source /path/to/private/bundle/vesc/app.lisp --repl /path/to/lispBM/repl/repl
```

Timed boost tests run without hardware:

```sh
LBM_REPL=/path/to/lispBM/repl/repl PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -p 'test_boost_*.py' -v
python3 -m unittest discover -s tests -p 'test_battery_current.py' -v
```

The native adapter/core tests build with address and undefined-behavior sanitizers. They exercise independent heartbeat/supply expiry, qualified release, boot cooldown, duplicate commands, pack changes, external writes, unload and failed restoration. The Lisp tests evaluate the actual adapter with mocked VESC native extensions. Supply tests verify that initial/stale pack observations, absent native telemetry, boot delay and intervening power loss cannot create leases. Builder tests preserve target resources and leave the policy disabled unless explicitly requested. See [timed boost](../tools/boost/README.md) for ARM SDK builds and the controller status format.

The TUNU 1.1 temperature test verifies that CAN status reports the firmware's measured FET temperature, including cold and overtemperature readings, while preserving the fault and reserved fields.

The audio-converter test uses a mocked FFmpeg command and no media files. The private-builder test verifies that default app source has no private sample configuration or song-7 dispatch, then checks private injection points and function replacement. The default warp-sample test verifies private playback is disabled and song 7 remains available to streamed songs. The private-build test evaluates the generated sample player in LispBM with VESC calls stubbed; it exercises concurrent generation identity, two-buffer scheduling under bounded wake lateness, final-buffer completion, same-song replay, stop-before-first-chunk, switching to MIDI, and stop during a refill wait. Generate the private app with the offline bundle tool before running that second command.

The runner extracts the current-limit definitions from `app.lisp` and executes them in LispBM with mocked ADC, RPM, voltage and configuration IO. It does not execute the application startup or contact hardware.

Coverage includes signed speeds across the standstill and fade thresholds, throttle/brake arbitration, KERS on/off, zero and nonzero KERS current, voltage taper, immediate drive-limit availability, and brake-limit restoration. The XML checks verify the ADC mode and deadband assumed by `drive-requested`.

A forward drive request selects the full configured brake-side motor-current envelope without the brake fade's slew delay. The ADC app still ramps the torque command. It permits at least 3 A of battery charging before voltage taper, independently of KERS; a larger enabled KERS allowance remains available. Firmware thermal, battery and BMS protections remain authoritative.

These tests validate Lisp policy, not motor response. Hardware validation must cover reverse-to-forward transitions with KERS on/off, throttle release and brake application, and battery voltages throughout the permitted range. They do not establish available torque at the battery charging limit or validate behavior on a full pack.
