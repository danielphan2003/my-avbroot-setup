#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$repo_root/make_ota.sh" \
    --device panther \
    --input "$1" \
    --ksu-block init_boot \
    --ksu-mode lkm \
    "${@:2}"
