# ADR 0011: Build the simulator image once in CI, scan it, bench it, then publish it

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

CI built the image inside the bench job with `make bench-up` and threw it away afterwards.
Nothing scanned it, and nothing kept it, so a cluster had no image to pull. The roadmap
named three gates: run the simulators as service containers, scan with Trivy, and push to
GitHub Container Registry.

## Decisions

- **One build, used three times.** An image job runs `make image`, scans the result and
  saves it as a workflow artifact. The bench job loads that artifact and starts the bench
  with `make bench-up BENCH_BUILD=--no-build`. The publish job pushes the same artifact. What
  is scanned is what is benched, and what is benched is what is published.
- **Compose, not service containers.** GitHub's service containers can't set a container's
  command, and the analyzer needs one (`--generator-admin signal-generator:5125`). Bending
  the image to read its role from the environment would change it only to suit CI. Compose
  already runs the bench locally and in CI, so it keeps doing so, now on the scanned image.
- **Trivy, pinned and checksum-verified, as gitleaks is.** Version 0.76.1 is downloaded from
  its GitHub release and checked against its SHA-256 before it runs. The `trivy-action` is not
  used: in March 2026 the Trivy ecosystem's supply chain was compromised (GHSA-69fq-xp46-6x23),
  and a pinned binary with a checked hash is a smaller thing to trust than a moving action tag. A checksum from the same release catches a
  corrupted or swapped download, not a compromised release; pinning a version that has been
  out for weeks is the guard against that.
- **Fail on what can be fixed.** The scan fails on a high or critical vulnerability that has
  a fixed version (`--ignore-unfixed`). One with no fix yet would block every change without
  anything to do about it.
- **Publish from `main` only, with an immutable tag.** After every other job passes on a push
  to `main`, the publish job pushes `ghcr.io/<owner>/virtual-rf-testbench-simulators:<version>-<commit>`.
  Pull requests build and scan but never push. There is no `latest` tag: a deployment pins
  one exact image.

- **Patch the image so it passes.** The first scan of the existing image found 9 fixable
  high vulnerabilities: 4 in Debian packages (`apt`, `libapt-pkg7.0`, `zlib1g`) and 5 in the
  `urllib3`, `msgpack` and `setuptools` copies vendored inside the base image's `pip`. The
  base image is repinned to the current `python:3.13.18-slim-trixie` digest. The runtime stage
  runs `apt-get upgrade` for Debian security fixes newer than that digest (`zlib1g`'s fix
  wasn't in it yet), and it uninstalls `pip`, which the simulators never use. The rescan finds
  none, and the bench suite passes 20 of 20 on the image loaded without a rebuild.

## Consequences

- `apt-get upgrade` makes a build depend on the day it runs: two builds of one commit can
  differ in Debian patch level. The scan, not the digest, is what guarantees the result.

- A vulnerable base image fails a pull request before it merges, and `make image-scan` runs
  the same scan locally (it needs Trivy installed).
- The bench job now waits for the image job, which adds the scan's time to the critical
  path.
- The tag-bump pull request is not built yet: it bumps an image tag in a chart's values
  file, and no chart exists. It comes with the cluster.
