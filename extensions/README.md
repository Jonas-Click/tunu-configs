# Battery-current events

These rules reconcile TUNU runtime limits when engine power turns on or a battery is inserted, removed or changes state. They require Librescoot event-service and Python 3 on the MDB.

The policy selects **150 A total battery current** only when both packs are present and active; otherwise it selects **80 A**. These are installation-specific limits, not a hardware rating. Battery admission remains battery-service's responsibility. This extension neither activates a pack nor changes engine power.

The helper uses CAN sender **251**, separate from ECU-service's 252 and the uploader's default 253. Install the current `tunu-flash.py`: its receive filter excludes broadcast traffic, and its REPL pacing respects the firmware's command interval.

After power-on, the helper polls with read-only Lisp queries until the interpreter responds, within a 20-second budget. It then reads fresh battery state, changes only the requested runtime limits, verifies tagged readback, and reconciles again if the active pack count changed. Concurrent event rules share a lock. A failed event-service action ends the whole sequence, so rule-level `repeat` is not a retry mechanism; readiness retries belong in the helper.

## Installation

Install these files as root, with the directory writable only by root:

| Repository file | MDB destination |
| --- | --- |
| `extensions/battery-current.py` | `/data/extensions/battery-current.py`, executable |
| `extensions/battery-current.toml` | `/data/extensions/battery-current.toml` |
| `tunu-flash.py` | `/data/extensions/tunu-flash.py` |

Back up existing files first. Restart **only** `librescoot-events` after replacing the rules; it does not hot-reload TOML. No ECU, vehicle-service or battery-service restart is needed. Check that both rules loaded. A service restart does not replay an already-on engine state: run the helper once to reconcile the current state after installation.

```sh
lsc ext list
lsc ext test tunu-battery-current-power-on --event '{"topic":"vehicle.engine-power.changed","to":"on"}'
/data/extensions/battery-current.py
journalctl -t tunu-battery-current --no-pager -n 20
```

`lsc ext test` is a dry run. Running the helper can change live battery-current and explicitly configured power limits. It never writes configuration to flash. Confirm the intended limits before enabling it on a vehicle.

## Optional power ceiling

Without `/data/extensions/battery-current.json`, the helper preserves the existing power ceiling. To explicitly select 8 kW on an installation validated for it:

```json
{"power_max_w": 8000}
```

The helper reapplies this ceiling after power-on, alongside the battery-count limit. Motor current, regenerative limits, thermal protection and motor calibration are unchanged. A power ceiling and battery-current ceiling both apply: at 55 V, 5 kW corresponds to about 91 A and 8 kW to about 145 A. The 80 A single-pack limit still bounds battery draw when an 8 kW power ceiling is configured.

The repository's `vesc_mcconf.xml` and `3kw-vesc_mcconf.xml` both specify 210 A motor current. Motor phase current is a separate limit from battery input current; changing the battery count alone does not require raising motor current.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_battery_current.py' -v
```

Tests cover startup readiness, bounded retries, engine-off handling, battery changes before and after writes, unchanged limits, optional power configuration and rejection of unrelated REPL output. They do not contact hardware.
