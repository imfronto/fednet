
# --- Overridden by this repo's pihole/Dockerfile ---
# Capabilities are already baked onto pihole-FTL above at image build
# time. This keeps upstream's own capsh detection and logging verbatim,
# but no longer treats a failed runtime setcap as fatal when the binary
# already carries the required file capabilities (checked with getcap,
# not assumed) -- the expected, harmless outcome under read_only.
fix_capabilities() {
    echo "  [i] Setting capabilities on pihole-FTL where possible"
    capsh --has-p=cap_chown 2>/dev/null && CAP_STR+=',CAP_CHOWN'
    capsh --has-p=cap_net_bind_service 2>/dev/null && CAP_STR+=',CAP_NET_BIND_SERVICE'
    capsh --has-p=cap_net_raw 2>/dev/null && CAP_STR+=',CAP_NET_RAW'
    capsh --has-p=cap_net_admin 2>/dev/null && CAP_STR+=',CAP_NET_ADMIN' || DHCP_READY='false'
    capsh --has-p=cap_sys_nice 2>/dev/null && CAP_STR+=',CAP_SYS_NICE'
    capsh --has-p=cap_sys_time 2>/dev/null && CAP_STR+=',CAP_SYS_TIME'

    if [[ ${CAP_STR} ]]; then
        echo "  [i] Applying the following caps to pihole-FTL:"
        IFS=',' read -ra CAPS <<<"${CAP_STR:1}"
        for i in "${CAPS[@]}"; do
            echo "        * ${i}"
        done

        setcap "${CAP_STR:1}"+ep "$(which pihole-FTL)" 2>/dev/null || ret=$?

        if [[ $DHCP_READY == false ]] && [[ $FTLCONF_dhcp_active == true ]]; then
            echo "ERROR: DHCP requested but NET_ADMIN is not available. DHCP will not be started."
            echo "      Please add cap_net_admin to the container's capabilities or disable DHCP."
            setFTLConfigValue dhcp.active false
        fi

        if [[ $ret -ne 0 && "${DNSMASQ_USER:-pihole}" != "root" ]]; then
            if getcap "$(which pihole-FTL)" 2>/dev/null | grep -q 'cap_net_bind_service' \
                && getcap "$(which pihole-FTL)" 2>/dev/null | grep -q 'cap_net_admin'; then
                echo "  [i] setcap write failed (expected under a read-only rootfs) but pihole-FTL already carries the required file capabilities from the image build -- continuing"
            else
                echo "  [!] ERROR: Unable to set capabilities for pihole-FTL, and the binary does not already carry them. Cannot run as non-root."
                exit 1
            fi
        fi
    else
        echo "  [!] ERROR: Unable to set capabilities for pihole-FTL."
        echo "            Please ensure that the container has the required capabilities."
        exit 1
    fi
    echo ""
}
