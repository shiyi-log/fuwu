#!/bin/sh
set -eu

# This include is consumed by nginx's geo module before any real-IP rewriting.
# Empty by default: arbitrary clients must never control the forwarded scheme.
output=/etc/nginx/fuwu-trusted-proxies.conf
: > "$output"
for cidr in ${FUWU_TRUSTED_PROXY_CIDRS:-}; do
  case "$cidr" in
    *[!0-9a-fA-F:./]*)
      echo 'Invalid FUWU_TRUSTED_PROXY_CIDRS entry; expected IP or CIDR only.' >&2
      exit 1
      ;;
  esac
  printf '%s 1;\n' "$cidr" >> "$output"
done
# nginx validates the address/CIDR syntax on startup; no config text is accepted.
