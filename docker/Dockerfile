# syntax=docker/dockerfile:1
#
# email-town-labeler container image.
#
#   docker build -f docker/Dockerfile -t email-town-labeler .                          # + onnx: runs town-modernbert
#   docker build -f docker/Dockerfile --build-arg EXTRAS= -t email-town-labeler:core .     # rules model only
#   docker build -f docker/Dockerfile --build-arg EXTRAS=embed,onnx -t email-town-labeler:full .  # + CPU torch
#
# All state (config, OAuth token, SQLite, models, logs) lives in the /data volume. First run:
#   docker compose run --rm email-town-labeler setup --headless --model town-modernbert

ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------- build stage
FROM ${PYTHON_IMAGE} AS build

# Comma-separated pip extras: "onnx" (default: the town-modernbert model), "", "embed", "embed,onnx".
ARG EXTRAS="onnx"

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# torch from the CPU-only index first: the default PyPI wheel bundles CUDA (several GB).
# Once torch is present, the normal install below sees the requirement as satisfied.
RUN case ",${EXTRAS}," in \
      *,embed,*|*,full,*) \
        pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.4" ;; \
    esac

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN if [ -n "${EXTRAS}" ]; then pip install "/src[${EXTRAS}]"; else pip install /src; fi \
 && email-town-labeler --version

# -------------------------------------------------------------- runtime stage
FROM ${PYTHON_IMAGE} AS runtime

ARG UID=10001
ARG GID=10001

LABEL org.opencontainers.image.title="email-town-labeler" \
      org.opencontainers.image.description="Private, self-hosted Gmail auto-labeling with a small local model" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/scamai/email-town-labeler"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EMAIL_TOWN_LABELER_HOME=/data \
    HF_HOME=/data/hf-cache

RUN groupadd --system --gid "${GID}" email_town_labeler \
 && useradd --system --uid "${UID}" --gid email_town_labeler --home-dir /data --no-create-home \
      --shell /usr/sbin/nologin email_town_labeler \
 && mkdir -p /data \
 && chown email_town_labeler:email_town_labeler /data \
 && chmod 0700 /data

COPY --from=build /opt/venv /opt/venv

USER email_town_labeler
WORKDIR /data
VOLUME ["/data"]

# Healthy = the CLI can read its config/state and report status. Before the first
# `login` this fails, which is the correct signal ("not set up yet").
HEALTHCHECK --interval=5m --timeout=30s --start-period=2m --retries=3 \
  CMD ["sh", "-c", "email-town-labeler status --json > /dev/null"]

ENTRYPOINT ["email-town-labeler"]
CMD ["run"]
