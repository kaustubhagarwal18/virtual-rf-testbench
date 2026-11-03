# ADR 0001: RsInstrument session settings, the driver test backend and own simulators

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

The drivers use RsInstrument 1.131.0 over a plain socket (`SelectVisa='socket'`), so no VISA
installation is needed. Before writing the simulators and drivers, two questions needed an
answer:

1. What does RsInstrument send when it opens a session, and what does it expect back?
2. Can the driver unit tests run RsInstrument on PyVISA-sim, as the plan suggested?

Both were tested with throwaway scripts: a logging socket server, a stateful server with an
error queue, and a PyVISA-sim YAML instrument.

## Findings

### What RsInstrument sends

| When | Commands sent |
|---|---|
| Opening a session | `*CLS`, `*ESE 1`, `*SRE 0`, `*CLS`, `*OPC?`, then `*IDN?` |
| With `reset=True` | Also `*RST`, `*OPC?`, `*CLS`, `*OPC?` |
| After every write or query | `*STB?` |
| When `*STB?` has bit 2 (0x04) set | `SYST:ERR?` until the queue reports `0,"No error"` |

- Commands are sent one per line, ending in `\n`.
- `*OPT?` is not sent by default.
- With `id_query=True`, RsInstrument rejects any `*IDN?` reply that doesn't name Rohde &
  Schwarz. The `SupportedIdnPatterns='VirtualRF'` option accepts our own identity, and
  RsInstrument then parses manufacturer, model and firmware version from it.
- A bad command raised `StatusException` with the instrument's message
  (`-113,"Undefined header"`), and the error queue was drained afterwards.

### Fake backends for unit tests

| Backend | Happy path | Bad command | Out-of-range value |
|---|---|---|---|
| PyVISA-sim via `SelectVisa='<file>.yaml@sim'` | Works | `ValueError`: the literal reply `ERROR` was read as the status byte | Not detected |
| PyVISA-sim via `direct_session` | Works | Same as above | Not detected |
| RsInstrument `Simulate=True` | Works | Not detected | Not detected |

PyVISA-sim has no SCPI error queue or status byte, and RsInstrument's simulation mode never
reports errors. Neither can test the driver's error handling.

## Decision

1. **The simulators implement the IEEE 488.2 status model RsInstrument relies on:** `*CLS`,
   `*ESE`, `*SRE`, `*OPC?`, `*STB?` with bit 2 set while the error queue is not empty, and
   `SYST:ERR?` returning numbered SCPI errors.
2. **The simulators identify as `VirtualRF`, not as a Rohde & Schwarz product.** The drivers
   open sessions with `SupportedIdnPatterns='VirtualRF'`.
3. **Error checking relies on RsInstrument's status check after every command**, which is
   stricter than one `SYST:ERR?` per command group. A bench test proves it by sending a bad
   command and expecting an error.
4. **Driver unit tests use a typed fake session** (a `ScpiSession` protocol with `write` and
   `query`) instead of PyVISA-sim, because only a fake can reproduce instrument errors. The
   bench tests cover the real RsInstrument and simulators over TCP.
5. **The bench runs against our own simulators, not PyVISA-sim.** The same findings rule
   PyVISA-sim out as the bench instruments. A regression suite has to see errors, status bits
   and timeouts the way it would on real hardware, and fault injection needs instruments whose
   behaviour can be changed at run time. Answers fixed in a YAML file can do neither.
   Our own asyncio simulators can.

## Consequences

- The plan's acceptance criterion "unit tests against PyVISA-sim definitions" is replaced by
  unit tests against a typed fake. PyVISA-sim is not a dependency.
- Every command costs one extra `*STB?` round trip. On a local socket this is negligible.
- An unknown query gets no reply, as on a real instrument, so the driver's timeout decides how
  long a test waits before failing. The timeouts need to be set deliberately.
