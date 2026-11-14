#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$repo_root/make_ota.sh" \
    --device caiman \
    --input "$1" \
    --magisk "$2" \
    --magisk-preinit-device "${3:-sda10}" \
    --modules custota,msd,bcr,oemunlockonboot,alterinstaller,tce \
    "${@:4}" \
    --module-tce "$HOME/src/github.com/eXtreames/TensorCardEmulator/app/build/distributions/debug/TCE-NONE.r3.gd40eb9c-debug.zip"
