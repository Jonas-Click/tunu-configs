# Lisp policy and telemetry tests

Run with Python 3 and a host LispBM REPL:

```sh
python3 tests/test_drive_limits.py --repl /path/to/lispBM/repl/repl
python3 tests/test_temperature.py --repl /path/to/lispBM/repl/repl
```

The TUNU 1.1 temperature test verifies that CAN status reports the firmware's measured FET temperature, including cold and overtemperature readings, while preserving the fault and reserved fields.

The runner extracts the current-limit definitions from `app.lisp` and executes them in LispBM with mocked ADC, RPM, voltage and configuration IO. It does not execute the application startup or contact hardware.

Coverage includes signed speeds across the standstill and fade thresholds, throttle/brake arbitration, KERS on/off, zero and nonzero KERS current, voltage taper, immediate drive-limit availability, and brake-limit restoration. The XML checks verify the ADC mode and deadband assumed by `drive-requested`.

A forward drive request selects the full configured brake-side motor-current envelope without the brake fade's slew delay. The ADC app still ramps the torque command. It permits at least 3 A of battery charging before voltage taper, independently of KERS; a larger enabled KERS allowance remains available. Firmware thermal, battery and BMS protections remain authoritative.

These tests validate Lisp policy, not motor response. Hardware validation must cover reverse-to-forward transitions with KERS on/off, throttle release and brake application, and battery voltages throughout the permitted range. They do not establish available torque at the battery charging limit or validate behavior on a full pack.
