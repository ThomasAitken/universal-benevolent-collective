# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb AS uv
FROM python:3.13.9-slim-bookworm@sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34

ARG DEBIAN_SNAPSHOT=20261125T000000Z
ARG CHROMIUM_VERSION=156.0.8078.108-1~deb12u1

ENV CHROME_PATH=/usr/bin/chromium \
    HOME=/run/ubc \
    PATH=/opt/ubc/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/run/ubc \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/ubc/venv \
    XDG_CACHE_HOME=/run/ubc/cache \
    XDG_CONFIG_HOME=/run/ubc/config

COPY --from=uv /uv /uvx /usr/local/bin/

RUN printf '%s\n' \
      "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} bookworm main" \
      "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT} bookworm-security main" \
      > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --no-install-recommends --yes "chromium=${CHROMIUM_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 ubc-browser \
    && useradd --uid 65532 --gid 65532 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin ubc-browser

WORKDIR /opt/ubc/app
COPY README.md pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev \
    && mkdir -p /run/ubc /var/lib/ubc/profile /var/lib/ubc/quarantine \
    && chown -R 65532:65532 /run/ubc /var/lib/ubc

USER 65532:65532

# The Linux launcher supplies a private control descriptor and egress endpoint.
ENTRYPOINT ["python", "-m", "ubc_node.browser.isolation.isolated_worker"]
