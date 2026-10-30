# Timed battery-current boost

Boost is optional and disabled in the generic `app.lisp`. The ordinary motor configurations keep their 60 A battery-input limit and 210 A phase-current limit. Both use the same Lisp boost parameters: at most 85 A per eligible active pack, or 170 A with two. These are aggregate controller limits scaled by pack count, not individual pack-current regulation or a guarantee of equal sharing. Boost never changes phase current, speed limits, regen, calibration, temperature protections or voltage protections. Existing power limits still apply and can limit the delivered current below the boost ceiling.

The positive current allowance is enforced by a small VESC native library, not a timer in the Lisp evaluator. Its independent thread restores the normal allowance on expiry, loss of the Lisp heartbeat, loss of supply evidence, a fault, rotation below -50 ERPM or disabled app output. Its unload callback restores normal before freeing the library. A failed restoration inhibits app output until recovery; a failed grant that successfully restores normal disables boost without disabling normal driving.

The native library is loaded through VESC's existing C-library interface. The timer itself needs no firmware change. The reviewed TUNU firmware has a 150 A battery-input hardware ceiling: 85 A single-pack is within that ceiling, but 170 A dual-pack requires a separate firmware ceiling change. If the SDK clamps the requested increase, the guard restores normal and inhibits boost rather than bypassing that limit. Build the guard against a reviewed 7.00 or 7.01 SDK; the builder checks the interface fingerprint and binary metadata. The two reviewed SDK interfaces produce the same binary. A matching 170 A ceiling build keeps the 50 A firmware factory default and all other hardware ranges unchanged; it does not enable boost or change installation limits. Other SDK versions need an ABI review before use.

## Parameters

The parameter block in `app.lisp` contains:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `boost-native-enabled` | false | Generated builds with the native import set this to true |
| `boost-policy-enabled` | false | Explicit opt-in to the policy |
| `boost-per-pack-amps` | 85 | Battery-input ceiling per eligible active pack, maximum 85 |
| `boost-duration-seconds` | 10 | Allowance per continuous drive interval, maximum 20 |
| `boost-cooldown-seconds` | 90 | Cooldown after exhaustion or interrupted active boost |
| `boost-release-ms` | 100 | Continuous released drive demand required for reset/rearm |
| `boost-heartbeat-ms` | 250 | Maximum age of the Lisp drive-demand lease |
| `boost-supply-ms` | 3000 | Maximum age of the supply-observation lease |

VESC's native configuration packets and VESC Tool XML presets configure existing motor/app fields; they do not add this timer policy. Native libraries can expose additional custom-config XML to VESC Tool, but that requires a separate schema and UI integration. This implementation keeps custom parameters in Lisp.

No boost transition writes flash. EEPROM slot 0 remains the odometer's storage.

## State and command behavior

The existing `0x4E0` byte 0 boost-request bit 1 is retained. Gear bit 0 and regen bit 2 remain independent. ECU-service is the normal sender; `settings[engine-ecu.boost]` supplies its requested state. Repeated control frames and enable/disable toggles cannot replenish a held-throttle budget.

A fresh native instance starts at the normal current limit with one cooldown interval, so a Lisp restart cannot bypass exhaustion. Once eligible, positive drive demand can use the allowance. The existing `drive-requested` ADC predicate defines drive demand, not zero RPM or a claimed equivalence to another controller's internal PWM state.

Releasing drive demand for the configured qualification time before exhaustion resets the interval. Exhaustion removes the extra allowance, waits for a qualified release, then starts cooldown. At cooldown completion the drive demand must be released to rearm. Disabling the request removes the extra allowance while preserving budget/cooldown state. No fresh evidence or no headroom above the normal allowance means no boost.

## Pack evidence and normal-limit ownership

`extensions/boost-supply.py` is an optional service. It does not power the controller, activate packs or change the boost-request bit. It waits six seconds after observing engine power on and requires a fresh, heartbeat-confirmed `engine-ecu:boost` envelope before opening CAN. It shares the maintenance/current lock, identifies the TUNU controller and renews the native supply lease using addressed VESC REPL traffic. It sends no music commands or new stock-protocol commands.

It leases only active, present, fault-free packs with ideal temperature state, no low-SoC flag and a recent changed snapshot for the same serial. The first snapshot is only a baseline. Unchanged snapshots expire instead of being treated as newly acquired data. These are observation-age bounds, not BMS acquisition timestamps. Constant readings may therefore suppress boost. Neither this service nor boost replaces battery-service's admission/protection policy.

The current reconciler supports an explicit `boost_guard` mode. It then reads and updates the native guard's **normal** allowance rather than writing over an active boost allowance. Other behavior, including optional power configuration, remains unchanged. Ordinary profile presets do not own this battery-current field; the native guard also checks for an intervening external write on each tick.

An example `/data/extensions/battery-current.json`, for an installation whose normal policy is 60 A single/120 A dual:

```json
{"single_current_a": 60, "dual_current_a": 120, "boost_guard": true}
```

The existing defaults remain 80 A single/150 A dual when no overrides are supplied. Choose the normal limits explicitly for each installation. `power_max_w` remains optional; boost does not lift it.

## Build without deployment

```sh
make -C tools/boost SDK=/path/to/bldc/lispBM/c_libs OUT=/tmp/boost-native
python3 tools/boost/build.py \
  --native-bin /tmp/boost-native/boost_guard.bin \
  --resource-backup /private/target-backup \
  --output-dir /private/boost-build
```

The resource backup is the same import-aware target backup used by the warp tooling. Output must stay outside this repository in a private directory. The builder preserves the target's native MIDI resources, checks complete image CRC/import alignment/capacity, and records the guard metadata. The default generated image still has the policy disabled. `--enable-policy` explicitly enables its Lisp policy; the ECU-service request and valid supply leases are still required.

Use `--source` for a reviewed target-specific source. Do not replace a target's distinct shutdown or temperature logic with another target's script. Sources with sampled warp audio can also be used once generated from the current app; sample files remain private.

An approved installation needs the built controller app, updated `battery-current.py`, `boost-supply.py`, `tunu-flash.py`, an explicit per-installation JSON policy and `tunu-boost-supply.service`. The service is not enabled by copying files into the repository. Register persistent unit restoration for the target's update workflow when deploying. Keep `engine-ecu.boost` false until stationary and controlled-load validation is authorized and complete.

## Controller status

Lisp application version 2 advertises the versioned extension on the existing eight-byte `0x7E3` status frame. Byte 0 retains the normal paired enable/disable bits. Byte 1 is `0xB1`.

| Bytes | Meaning |
| --- | --- |
| 2, bits 0-2 | State: 0 disabled, 1 armed, 2 active, 3 waiting release, 4 cooldown, 5 inhibited |
| 2, bit 3 | Request accepted/enabled |
| 2, bit 4 | Extra allowance active |
| 2, bits 5/6 | Lisp heartbeat/supply lease fresh |
| 2, bit 7 | Reserved, zero |
| 3, bits 0/1 | Eligible pack mask |
| 3, bits 2-7 | Reason: 0 none, 1 heartbeat expired, 2 supply unavailable, 3 motor inhibited, 4 no headroom, 5 limit-write failure |
| 4-5 | Remaining allowance in 100 ms units, big-endian |
| 6-7 | Remaining cooldown in 100 ms units, big-endian |

Native heartbeat callbacks send status every 200 ms. CAN transmission runs outside the guard mutex and never in the safety worker, so blocked telemetry cannot delay current expiry. If Lisp stops, telemetry expires while the native worker restores normal. Unload emits a final non-active report and stops the native thread. Old clients retain the compatibility enable acknowledgement. The matching ECU-service decoder exposes actual allowance state separately at `engine-ecu:boost`, with observation timestamps and a 500 ms expiry. Enabled does not mean the extra allowance is active or that the motor is drawing the ceiling current.

## Tests

```sh
LBM_REPL=/path/to/host/lbm PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tests/test_drive_limits.py --repl /path/to/host/lbm
python3 tests/test_warp_samples.py --repl /path/to/host/lbm
```

Host C tests use address/undefined-behavior sanitizers. They cover timing boundaries, qualified release, cooldown, duplicate commands, tick wrap, pack changes, interleaved limit writes, native heartbeat expiry without Lisp, blocked telemetry, unload, faults, hardware clamping and failed restoration. The Lisp tests execute the actual adapter with mocked native calls. The builder and supply-observation tests do not contact hardware. Higher-current loaded testing and deployment require separate authorization.
