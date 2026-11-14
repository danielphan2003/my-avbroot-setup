#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The TensorCardEmulator module zip. Set TCE_MODULE on machines that do not
# build it in the checkout below (supercomputer keeps a copy in module/).
tce_module="${TCE_MODULE:-$HOME/src/github.com/eXtreames/TensorCardEmulator/app/build/distributions/release/TCE-NONE.r3.gd40eb9c-release.zip}"
exec "$repo_root/make_ota.sh" \
    --device caiman \
    --input "$1" \
    --modules custota,msd,bcr,oemunlockonboot,alterinstaller,tce \
    "${@:2}" \
    --module-tce "$tce_module"
