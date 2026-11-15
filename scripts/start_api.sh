#!/bin/sh
set -eu

# The Compose deployment must never silently use a development or example key.
case "${DJANGO_SECRET_KEY:-}" in
  ''|fuwu-local-development-key|change-this-for-local-use)
    echo 'Configure a random DJANGO_SECRET_KEY before starting the deployed API.' >&2
    exit 1
    ;;
esac
if [ "${#DJANGO_SECRET_KEY}" -lt 32 ]; then
  echo 'DJANGO_SECRET_KEY must contain at least 32 characters for deployment.' >&2
  exit 1
fi
exec uv run --frozen --no-dev fuwu-server
