# Adapting the project to your own bench

Out of the box, the triage assistant knows the nine simulated faults in this repository, and
the drivers talk only to the simulated instruments. This page covers the three ways to make
it yours, from least to most work. Setup is in the [README](../README.md).

| You want to | Change | Code changes |
|---|---|---|
| Triage your own failures | Feed in your resolved runs as history | None |
| Teach it a new kind of fault | The fault catalogue and the domain pack's actions | A few lines, plus a re-run of `make eval` |
| Drive real instruments | The drivers' identity filter and command set | Yes, and testing on the hardware |

## 1 · Triage your own failures

The assistant answers from the past runs most like the failure it is shown, so its knowledge
is its history. Give it your own resolved failures and it will cite them.

**What a run is.** A results file in the bench suite's format, plus, optionally, the
instruments' log lines and the setup sequence's log lines. `make bench-test` writes one to
`reports/bench-results.json`. A minimal one the API accepts:

```json
{
  "tests": [
    {
      "id": "tests/bench/test_bench.py::test_level_accuracy",
      "outcome": "failed",
      "message": "level_1000mhz: -12.9 dBm, limit -11 to -9 dBm, margin -1.9 dB",
      "measurements": [
        {"name": "level_1000mhz", "value": -12.9, "unit": "dBm",
         "limit_low": -11.0, "limit_high": -9.0, "margin": -1.9, "passed": false}
      ]
    }
  ],
  "firmware_versions": {"analyzer": "2.1.0", "generator": "2.1.0"}
}
```

**What a resolved run adds.** A label saying how it was fixed: `fault` (a short name),
`cause`, `fix`, and optionally `action`, a key from the domain pack's action list
(`CABLE`, `LINK`, `ALIGNMENT`, `CONFIGURATION`, `TIMEOUT`, `TEST_CLEANUP`, `FIRMWARE`). A run
that records its action gives the model the action itself, not only a description.

**Two ways to load them:**

- **Over HTTP, while the service runs.** Start it with `make triage-api`, then post each
  resolved run to `POST /history`. This example sends one of the repository's own history
  runs:

  ```
  jq -n --slurpfile results triage/data/runs/history/cable_loss-1/results.json --slurpfile label triage/data/runs/history/cable_loss-1/label.json '{results: $results[0], label: ($label[0] | {fault, cause, fix, action})}' | curl -s -X POST http://127.0.0.1:8000/history -H 'Content-Type: application/json' -d @-
  ```

  The history lives in memory, so it is gone when the service stops.
- **From a folder, at start-up.** Make one folder per run, holding `results.json`,
  `label.json` and, if you have them, `logs.txt` and `sequence.log`, as in
  `triage/data/runs/history/`. Then point the service at it:

  ```
  uv run --extra api python -m triage.api --history path/to/your/history
  ```

Then post a failed run to `POST /triage`, as the [usage guide](USAGE.md) shows. The answer
cites your past runs and quotes their recorded fix.

## 2 · Teach it a new kind of fault

A fault with a known cause is three things: a way to cause it on the bench, a label, and an
action on the fixed list.

- **The fault:** add an entry to `CATALOGUE` in `tools/faults.py`, as an `AdminFault`
  (switched on an instrument's admin port), a `ConfigFault` (a bench configuration value)
  or a `ReleaseFault` (a simulated firmware release). Its `Label` records the cause, the fix
  and the action.
- **The action, if none fits:** add an `[[actions]]` entry to the domain pack,
  `triage/packs/rf-bench.toml`: a key, the cause it answers (one of the pack's
  `categories`) and what it says to do. No code changes; the pack is checked when it loads,
  and a test checks every fault's category against its action's, so the two can't drift
  apart.
- **The data:** `make fault-dataset` rebuilds the history and question runs for every fault,
  then `make eval` scores the assistant on them.

Adding an action, or changing what one says, changes the instructions every question is
asked with, so `make eval` re-asks the model every question rather than reading its cache,
and `tests/unit/triage/test_behaviour_lock.py` fails until its hashes are recorded again with
`UPDATE_BEHAVIOUR_LOCK=1`. Expect that to take a while
on a laptop.

## 3 · Drive real instruments

The drivers in `drivers/` speak SCPI through RsInstrument, so the same code can reach a
real instrument over the network. Two things keep them on the simulators today:

- **The identity filter.** `drivers/session.py` opens every session with
  `SupportedIdnPatterns='VirtualRF'`, so a session refuses any instrument that doesn't
  identify as a simulator. This is deliberate: it stops the suite from changing the settings
  of a real instrument by accident. In your own fork, change it to match your instrument's
  `*IDN?` reply.
- **The command set.** The simulators implement a subset of SCPI (listed in the
  [usage guide](USAGE.md)). A real instrument may name or bound its commands differently,
  so check each driver method against its programming manual.

Run against real hardware at your own risk, on a bench where changing the output level and
frequency is safe.
