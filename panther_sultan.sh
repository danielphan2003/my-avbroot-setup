#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_OUT="$HOME/src/github.com/kerneltoast/android_kernel_google_gs201/out/arch/arm64/boot"

exec "$repo_root/make_ota.sh" \
    --device panther \
    --input "$1" \
    --kernel-boot "$KERNEL_OUT/Image" \
    --kernel-dtbo "$KERNEL_OUT/dts/google/dtbo.img" \
    "${@:2}"
