#!/bin/sh
set -e
cd "$(dirname "$0")"

rm -rf web/core
cp -r core web/core
find web/core -name __pycache__ -type d -prune -exec rm -rf {} +

if [ "$RENDER" = "true" ] && [ -z "$GOOGLE_CLIENT_ID" ]; then
  echo "GOOGLE_CLIENT_ID is not set. Add it under Environment in the Render dashboard." >&2
  exit 1
fi

if [ -n "$GOOGLE_CLIENT_ID" ]; then
  case "$GOOGLE_CLIENT_ID" in
    *[!A-Za-z0-9._-]*) echo "GOOGLE_CLIENT_ID contains unexpected characters." >&2; exit 1 ;;
    *.apps.googleusercontent.com) ;;
    *) echo "GOOGLE_CLIENT_ID should end with .apps.googleusercontent.com." >&2; exit 1 ;;
  esac
  printf 'window.APP_CONFIG = { clientId: "%s" };\n' "$GOOGLE_CLIENT_ID" > web/config.js
  echo "client id written to web/config.js"
fi

echo "built web/"
