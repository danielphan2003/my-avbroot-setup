#!/bin/bash
./make_ota.sh \
    --device caiman \
    --input "$1" \
    --magisk "$2" \
    --magisk-preinit-device "${3:-sda10}" \
    "${@:4}"
