# Bakes pihole-FTL's file capabilities at build time so `read_only: true`
# can be added to the pihole service (services/pihole.yml) without
# depending on the base image's own fix_capabilities() repeating the same
# `setcap` write on every container start — a write a read-only rootfs
# blocks outright. This is exactly the failure that reverted read_only
# twice before (see AUDIT_REPORT.md's Pi-hole incident history): FTL's
# own startup treats that failure as fatal.
#
# fix_capabilities() is overridden below, not patched in place: bash
# sources bash_functions.sh top to bottom and the last definition of a
# function wins, so appending a new one here fully replaces the original
# without text-matching (and risking silent drift against) its internals.
# The grep guard makes any future upstream change to that error string a
# loud build failure instead of a silently stale override.
FROM pihole/pihole:latest@sha256:f7d1be836e3bc608b56d82fc9904f5a831cdfbc0dc9c6d58f94e4c985c70038b

RUN grep -q 'Cannot run as non-root' /usr/bin/bash_functions.sh \
    || { echo "fix_capabilities()'s expected error string is gone from this image -- re-check pihole/Dockerfile's override against the new bash_functions.sh before building"; exit 1; }

# Same capability set as services/pihole.yml's cap_add, computed once here
# instead of guessed: it's exactly what capsh --has-p would detect given
# that cap_add list, per fix_capabilities()'s own probing logic below.
RUN setcap 'cap_chown,cap_net_bind_service,cap_net_raw,cap_net_admin+ep' /usr/bin/pihole-FTL

COPY fix_capabilities_override.sh /tmp/fix_capabilities_override.sh
RUN cat /tmp/fix_capabilities_override.sh >> /usr/bin/bash_functions.sh \
    && rm /tmp/fix_capabilities_override.sh
