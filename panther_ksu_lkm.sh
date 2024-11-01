#!/bin/bash

./make_ota.sh \
    --device panther \
    --input "$1" \
    --ksu-block init_boot \
    --ksu-mode lkm \
    "${@:2}"
