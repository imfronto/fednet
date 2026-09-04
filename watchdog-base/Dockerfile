# Shared base for the 4 watchdog/digest scripts (gravity, honeypot,
# pihole-flood, pihole-stats). Bakes in the packages each script's own
# `apk add` line was installing at container *start* — meaning a restart
# during an internet blip previously left the watchdog running with no
# curl/jq and no way to surface that (see scripts/*_watchdog.sh history).
FROM alpine:latest@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
RUN apk add --no-cache curl jq coreutils
