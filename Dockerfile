# syntax=docker/dockerfile:1.7
# Two images from one file. The default target, runtime (last), is the simulator image: one
# image for both simulators (ADR 0004), and Compose picks which one runs. The triage-runtime
# target is the triage API (ADR 0028); Compose builds it only under the triage profile.
# Build arguments: FIRMWARE_VERSION (what *IDN? reports, and the project version both images
# are labelled with) and REVISION (the git commit).

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# Triage build stage: the API's locked dependencies (numpy, and the api extra's FastAPI and
# uvicorn) into /opt/venv, and the triage package compiled to bytecode. The project itself
# isn't installed: its wheel holds the simulators and drivers, which the API never imports.
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS triage-build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra api --no-install-project
COPY triage ./triage
# Unchecked-hash bytecode stays valid whatever the copied files' timestamps, so the read-only
# container never needs to write a .pyc.
RUN /opt/venv/bin/python -m compileall -q --invalidation-mode unchecked-hash triage

# Triage runtime stage: the API, its seed history and its packs, run as an unprivileged user.
# Added runs and cached vectors go to the SQLite file on the /data volume (ADR 0027).
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS triage-runtime
ARG FIRMWARE_VERSION=""
ARG REVISION="unknown"
LABEL org.opencontainers.image.title="virtual-rf-testbench triage" \
      org.opencontainers.image.description="Triage API: is a failed bench run the firmware, the bench or the test?" \
      org.opencontainers.image.source="https://github.com/kaustubhagarwal18/virtual-rf-testbench" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${FIRMWARE_VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
# The same hardening as the simulator image below: security fixes applied at build time, the
# base image's pip removed, and a system user with no home or shell. /data is created owned by
# that user, so a new named volume mounted there starts out writable by it.
RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes --root-user-action=ignore pip \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin triage \
    && mkdir /data \
    && chown triage:triage /data
COPY --from=triage-build /opt/venv /opt/venv
COPY --from=triage-build /src/triage /app/triage
# The seed history's default path, triage/data/runs/history, is relative to /app.
WORKDIR /app
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TRIAGE_HISTORY_DB=/data/history.sqlite
VOLUME /data
USER 10001
EXPOSE 8000
# Liveness only: /livez never asks Ollama, so an Ollama outage never marks the container
# unhealthy. The standard library's urllib, because the slim base has no curl.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --start-interval=1s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=2)"]
CMD ["python", "-m", "triage.api", "--host", "0.0.0.0"]

# Simulator build stage: install the locked runtime dependencies and the project into /opt/venv.
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY simulators ./simulators
COPY drivers ./drivers
COPY measurements ./measurements
RUN uv sync --locked --no-dev --no-editable

# Simulator runtime stage (the default target): the environment only, run as an unprivileged user.
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime
ARG FIRMWARE_VERSION=""
ARG REVISION="unknown"
LABEL org.opencontainers.image.title="virtual-rf-testbench simulators" \
      org.opencontainers.image.description="Simulated VirtualRF SG-1 signal generator and SA-1 spectrum analyzer" \
      org.opencontainers.image.source="https://github.com/kaustubhagarwal18/virtual-rf-testbench" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${FIRMWARE_VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
# Debian security fixes newer than the pinned base image are applied at build time; the
# Trivy scan in CI is what checks they are there. The simulators run from /opt/venv and never
# install anything, so the base image's pip, and the copies of urllib3 and msgpack it vendors,
# are removed rather than patched.
RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes --root-user-action=ignore pip \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin simulator
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIMULATOR_FIRMWARE_VERSION="${FIRMWARE_VERSION}"
USER 10001
EXPOSE 5025 5125 5126
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --start-interval=1s --retries=3 \
    CMD ["python", "-m", "simulators.healthcheck", "--port", "5025"]
CMD ["python", "-m", "simulators.signal_generator", "--host", "0.0.0.0"]
