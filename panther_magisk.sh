#!/bin/bash

./make_ota.sh \
    --device panther \
    --input "$1" \
    --magisk "$2" \
    --magisk-preinit-device "${3:-metadata}" \
    "${@:4}"

