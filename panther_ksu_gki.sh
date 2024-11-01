#!/bin/bash

./make_ota.sh \
    --device panther \
    --input "$1" \
    --kernel-zip "$2" \
    --ksu-mode gki \
    "${@:3}"
