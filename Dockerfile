# syntax=docker/dockerfile:1.7
# One image for both simulators (ADR 0004). Compose picks which one runs.
# Build arguments: FIRMWARE_VERSION (what *IDN? reports) and REVISION (the git commit).

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# Build stage: install the locked runtime dependencies and the project into /opt/venv.
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

# Runtime stage: the environment only, run as an unprivileged user.
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
