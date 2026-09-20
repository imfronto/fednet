# Multi-stage build: the builder stage has pip/build tooling, the final
# image only has the installed package and its runtime dependencies —
# keeps the shipped image smaller and avoids carrying a compiler around.
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
# Runs as a dedicated, unprivileged, non-root user (requirement #20) with no
# shell and no home directory it could write to outside the data volume.
# Fixed numeric UID/GID (not auto-allocated) so bind-mount ownership on the
# host is predictable — see docs/DEPLOYMENT.md's "chown the data directory"
# step.
RUN groupadd --gid 10001 guardian \
    && useradd --uid 10001 --gid guardian --no-create-home --shell /usr/sbin/nologin guardian \
    && mkdir -p /data/pihole-guardian \
    && chown guardian:guardian /data/pihole-guardian

COPY --from=builder /install /usr/local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GUARDIAN_DATABASE_PATH=/data/pihole-guardian/guardian.db \
    GUARDIAN_PROTECTED_DOMAINS_PATH=/data/pihole-guardian/protected_domains.yml

USER guardian
WORKDIR /data/pihole-guardian
EXPOSE 8930

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8930/api/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "pihole_guardian.main"]
