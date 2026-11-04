# ADR 0003: RsInstrument rather than plain PyVISA, behind a small session interface

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

The drivers need a library to send SCPI to the instruments. The two candidates are R&S's
RsInstrument and plain PyVISA. ADR 0001 recorded how RsInstrument behaves against the
simulators; this ADR records why it was chosen, and how the drivers avoid depending on it.

## Options considered

| | RsInstrument 1.131 | Plain PyVISA |
|---|---|---|
| Raw socket without a VISA install | Built in (`SelectVisa='socket'`) | Needs the pyvisa-py backend |
| Error queue check after each command | Built in: reads `*STB?`, drains `SYST:ERR?`, raises | Has to be written and called by hand |
| `*OPC?` synchronisation, timeouts, logging | Built in | Partly; the rest by hand |
| Used on real R&S benches | Yes: R&S's own library | Widely used, vendor-neutral |
| Accepts non-R&S instruments | Only with `SupportedIdnPatterns` | Yes |
| Exceptions | Its own (`StatusException`, `TimeoutException`) | PyVISA's |

## Decision

1. **Use RsInstrument.** Its status check after every command enforces the project's
   "never swallow instrument errors" rule without any driver code, and it is what an R&S
   test team uses.
2. **Hide it behind `ScpiSession`** (`write` and `query`), in `drivers/session.py`.
   `RsInstrumentSession` is the only class that imports RsInstrument. It turns every
   RsInstrument and socket error into `InstrumentError` or `InstrumentTimeoutError`, so
   drivers and tests never see RsInstrument's exceptions.
3. **A timeout that also left instrument errors is a timeout.** RsInstrument reads the error
   queue after a timeout and raises a `StatusException` whose `first_exc` is
   `TimeoutException`; the session reports that as `InstrumentTimeoutError`, with the queued
   errors (such as `-113`) in its message.

## Consequences

- Driver unit tests run on a typed fake session with no network (ADR 0001).
- Switching to PyVISA, or to a real instrument's HiSLIP transport, means one new session
  class; the drivers and tests don't change.
- RsInstrument leaves a socket open when a connection is refused (seen as a
  `ResourceWarning` in the test that connects to a closed port). It only happens on that
  failure path, and the operating system reclaims the socket.
