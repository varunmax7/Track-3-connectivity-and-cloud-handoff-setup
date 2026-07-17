#!/usr/bin/env bash
# Network hardening script — idempotent, ready for real board.
# Usage: ./harden.sh [--dry-run] [--gateway-port PORT]
set -euo pipefail

DRY_RUN=0
GATEWAY_PORT=8443
SSH_PORT=22

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --gateway-port) GATEWAY_PORT="$2"; shift ;;
        --ssh-port) SSH_PORT="$2"; shift ;;
    esac
    shift
done

emit() {
    echo "$@"
}

apply() {
    if [[ $DRY_RUN -eq 1 ]]; then
        emit "DRY-RUN: $*"
    else
        "$@"
    fi
}

# ── SSH hardening ───────────────────────────────────────────────────────────

SSHD_CONFIG="/etc/ssh/sshd_config"

sshd_set() {
    local key="$1" val="$2"
    if [[ $DRY_RUN -eq 1 ]]; then
        emit "SSHD: ${key} ${val}"
    else
        if grep -qE "^#?${key}" "$SSHD_CONFIG"; then
            sed -i "s|^#*\s*${key}.*|${key} ${val}|" "$SSHD_CONFIG"
        else
            echo "${key} ${val}" >> "$SSHD_CONFIG"
        fi
    fi
}

emit "==> Hardening SSH"
sshd_set "PasswordAuthentication" "no"
sshd_set "PubkeyAuthentication" "yes"
sshd_set "PermitRootLogin" "no"
sshd_set "ChallengeResponseAuthentication" "no"
sshd_set "UsePAM" "no"
sshd_set "X11Forwarding" "no"
sshd_set "MaxAuthTries" "3"
sshd_set "LoginGraceTime" "20"

if [[ $DRY_RUN -eq 0 ]]; then
    systemctl reload ssh 2>/dev/null || true
fi

# ── Firewall (ufw) ──────────────────────────────────────────────────────────

emit "==> Configuring firewall"
apply ufw --force reset
apply ufw default deny incoming
apply ufw default allow outgoing
apply ufw limit "${SSH_PORT}/tcp"
apply ufw allow "${GATEWAY_PORT}/tcp"
apply ufw --force enable

emit "==> Hardening complete. SSH=${SSH_PORT}, gateway=${GATEWAY_PORT}"
