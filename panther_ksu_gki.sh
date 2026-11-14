#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$repo_root/make_ota.sh" \
    --device panther \
    --input "$1" \
    --kernel-zip "$2" \
    --ksu-mode gki \
    "${@:3}"
