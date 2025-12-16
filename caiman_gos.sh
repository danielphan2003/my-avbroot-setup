#!/bin/bash
./make_ota.sh \
    --device caiman \
    --input "$1" \
    "${@:2}"
