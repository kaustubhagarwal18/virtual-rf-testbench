# ADR 0002: The analyzer reads the generator's state from the generator's admin port

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

On a real bench, a cable carries the generator's signal to the analyzer. The simulators are
two separate services, as the spec requires, so the analyzer needs another way to learn what
the generator is sending (frequency, level and RF on or off) before the physics model can
turn it into a reading.

## Options considered

| Option | For | Against |
|---|---|---|
| Analyzer queries the generator over SCPI | No new interface | A second controller on the generator: its queries and RsInstrument's `*STB?` checks would mix with the test's own session, and real analyzers never control generators |
| One process simulates both instruments | Simplest; no network hop | Breaks the spec's two separate services, and the instruments could no longer run in separate containers |
| A shared store, such as Redis | Decoupled | A whole new dependency standing in for a cable |
| **The generator's admin port** | Out of band, like the fault switches that will share it; the SCPI interface stays exactly what a real instrument offers | One extra local round trip per measurement |

## Decision

The generator offers a separate **admin port** (5125 by default) that answers one line with
one line of JSON. `STATE?` returns:

```json
{"frequency_hz": 2500000000.0, "level_dbm": -10.0, "output_on": true}
```

The analyzer asks for it on every measurement, so a reading always reflects the generator's
current settings. Test code never uses the admin port; only the simulators and, from Week 2,
the fault tooling do.

## Consequences

- The analyzer needs the generator's admin address as a setting.
- The admin port must not be reachable from outside the bench network. Week 7 protects it
  with a token from OpenBao, as the spec requires.
- Fault switches (Week 2) become further admin commands registered next to `STATE?`, so
  injecting a fault never touches the SCPI interface.
