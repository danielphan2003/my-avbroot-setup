#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$repo_root/make_ota.sh" \
    --device panther \
    --input "$1" \
    --magisk "$2" \
    --magisk-preinit-device "${3:-metadata}" \
    "${@:4}"
