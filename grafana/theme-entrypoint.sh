#!/bin/sh
set -eu

# Grafana OSS has no supported custom-CSS/white-labeling hook (that's an
# Enterprise-only feature) — this patches the vendored index.html to add
# one, the well-known community workaround. It's inherently coupled to
# Grafana's internal template structure and can break on an image
# upgrade; the marker check below just makes re-running it on container
# restart safe, not the patch itself durable across Grafana versions.
INDEX="/usr/share/grafana/public/views/index.html"
MARKER="fednet-custom.css"

if ! grep -q "$MARKER" "$INDEX"; then
  sed -i "s#</head>#<link rel=\"stylesheet\" href=\"/public/${MARKER}\" />\n  </head>#" "$INDEX"
fi

# The patch above needs root (index.html isn't writable by grafana's
# normal uid 472), but the server itself shouldn't run as root — drop
# down before exec'ing it. No gosu/su-exec in this image, so plain
# busybox su.
exec su -s /bin/sh grafana -c /run.sh
