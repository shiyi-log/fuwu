#!/usr/bin/env bash
# Host-only account recovery. Passwords are read by Python from the terminal.
set -euo pipefail

FUWU_SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FUWU_UV_OPTIONS=(--project "$FUWU_SCRIPT_ROOT" --frozen)
if [[ -f "$FUWU_SCRIPT_ROOT/.env" ]]; then
  FUWU_UV_OPTIONS+=(--env-file "$FUWU_SCRIPT_ROOT/.env")
fi
exec uv run "${FUWU_UV_OPTIONS[@]}" python -m fuwu_service.accounts reset-password "$@"
