#!/bin/sh
# Writes config.json from the image's template with this container's
# deployment values (run by the nginx image's entrypoint before nginx starts).
set -eu
root=/usr/share/nginx/html
# shellcheck disable=SC2016  # the literal variable names are envsubst's allowlist
envsubst '${HELPER_PORT} ${DHTB_URL}' < "$root/config.json.template" > "$root/config.json"
