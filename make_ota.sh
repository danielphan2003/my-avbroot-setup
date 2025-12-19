#!/bin/bash

LOG_PREFIX="[$(basename "$0")] "
LOG_LEVEL="INFO"

# uncomment if you don't want to include utils.sh
# get_time() { echo "$(date +%s.%N)" }
# setup_log() { START=$(get_time); export START }
# log() { printf "%s%19s %16s %s\n" "$LOG_PREFIX" "$(printf "%.3fs" "$(bc <<< "$(get_time) - $START")")" "$1" "$2" }

. "utils.sh" 2>/dev/null || {
    if ! declare -F setup_log >/dev/null || ! declare -F get_time >/dev/null || ! declare -F log >/dev/null; then
        echo "$(tput bold)$LOG_PREFIX$(tput sgr0) $(tput setaf 8)0.000s$(tput sgr0) $(tput setaf 1)ERROR$(tput sgr0) Please uncomment get_time, setup_log, log OR include utils.sh"
        exit 1
    fi
}

setup_log

if [[ -z "$PASS_AVB_ENV_VAR" ]] || [[ -z "$PASS_OTA_ENV_VAR" ]] || [[ -z "$GH_TOKEN" ]]; then
    log ERROR "Please set PASS_AVB_ENV_VAR, PASS_OTA_ENV_VAR and GH_TOKEN"
    exit 1
fi

ADDED_FLAGS=()
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --device)
            DEVICE="$2"
            shift 2
            ;;
        --input)
            INPUT="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --kernel-zip)
            KERNEL_ZIP="$2"
            shift 2
            ;;
        --kernel-boot)
            KERNEL_BOOT="$2"
            shift 2
            ;;
        --kernel-dtbo)
            KERNEL_DTBO="$2"
            shift 2
            ;;
    	--ksu-block)
    	    KSU_BLOCK="$2"
    	    shift 2
    	    ;;
        --ksu-mode)
            KSU_MODE="$2"
            shift 2
            ;;
        --magisk)
            MAGISK="$2"
            shift 2
            ;;
        --magisk-preinit-device)
            MAGISK_PREINIT_DEVICE="$2"
            shift 2
            ;;
        --device-dir)
            DEVICE_DIR="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --module-dir)
            MODULE_DIR="$2"
            shift 2
            ;;
        --extract-dir)
            EXTRACT_DIR="$2"
            shift 2
            ;;
        --verbose|-v)
            LOG_LEVEL=DEBUG
            shift 1
            ;;
        *)
            ADDED_FLAGS+=("$1")
            shift 1
            ;;
    esac
done

PRJ_ROOT="$(pwd)"
DEVICE_DIR="${DEVICE_DIR:-device}"
DEVICE_PATH="$DEVICE_DIR/$DEVICE"
OUTPUT_DIR="${OUTPUT_DIR:-"ota/$DEVICE"}"
INPUT_BASENAME="$(basename "$INPUT")"
INPUT_BASENAME="${INPUT_BASENAME/.zip/}"
DATE="$(date +%Y-%m-%d_%H-%M-%S)"
OUTPUT="$OUTPUT_DIR/$INPUT_BASENAME.$DATE"
MODULE_DIR="${MODULE_DIR:-module}"
EXTRACT_DIR="${EXTRACT_DIR:-extracted}"
EXTRACTED="${EXTRACT_DIR}/$INPUT_BASENAME"
FLAGS=()

if [[ "${#ADDED_FLAGS[@]}" -ne 0 ]]; then
    FLAGS+=("${ADDED_FLAGS[@]}")
    log INFO "Flags added:" "${ADDED_FLAGS[@]}"
fi

if [[ ! -f "$INPUT" ]]; then
    log ERROR "Invalid input OTA: $INPUT"
    exit 1
fi

if [[ ! -d "$EXTRACT_DIR" ]]; then
    mkdir -p "$EXTRACT_DIR" || {
	log ERROR "Invalid extract dir: $EXTRACT_DIR (not a directory)"
        exit 1
    }
    log INFO "Created extract dir: $EXTRACT_DIR"
fi

if [[ -f "$MAGISK" ]] && [[ -n "$MAGISK_PREINIT_DEVICE" ]]; then
    log INFO "Using magisk ($MAGISK), preinit device ($MAGISK_PREINIT_DEVICE)"

    ADDED_FLAGS=(--magisk "$MAGISK" --magisk-preinit-device "$MAGISK_PREINIT_DEVICE")
    FLAGS+=("${ADDED_FLAGS[@]}")
    log DEBUG "Flags added:" "${ADDED_FLAGS[@]}"

    OUTPUT="$OUTPUT.magisk"
    log DEBUG "Current expected OTA output: $OUTPUT"
elif [[ -v MAGISK ]] || [[ -v MAGISK_PREINIT_DEVICE ]]; then
    log WARN "Invalid magisk ($MAGISK), preinit device ($MAGISK_PREINIT_DEVICE)"
fi

if [[ ! -f "$EXTRACTED/avb_pkmd.bin" ]]; then
    log INFO "Extracting original OTA to $EXTRACTED"
    avbroot ota extract --input "$INPUT" --directory "$EXTRACTED" --all

    msg="Extracting public AVB key from original OTA"
    [[ "$LOG_LEVEL" = "DEBUG" ]] && msg="$msg to $EXTRACTED/avb_pkmd.bin"
    log "$LOG_LEVEL" "$msg"

    log DEBUG "Extracting vbmeta image and parsing public AVB key"
    avbroot avb info -i "$EXTRACTED/vbmeta.img" \
        | grep 'public_key' \
        | sed -n 's/.*public_key: "\(.*\)".*/\1/p' \
        | tr -d '[:space:]' | xxd -r -p > "$EXTRACTED/avb_pkmd.bin"
fi

log INFO "SHA256 of public AVB key from original OTA: $(sha256sum "$EXTRACTED"/avb_pkmd.bin)"

if [[ ! -f "$EXTRACTED/ota.crt" ]]; then
    msg="Extracting OTA cert from original OTA"
    [[ "$LOG_LEVEL" = "DEBUG" ]] && msg="$msg to $EXTRACTED/ota.crt"
    log "$LOG_LEVEL" "$msg"
    unzip -j "$INPUT" META-INF/com/android/otacert && mv -v otacert "$EXTRACTED"/ota.crt
fi

if [[ -f "$KERNEL_ZIP" ]]; then
    if [[ $KSU_MODE = gki ]]; then
        log INFO "Extracting kernel zip for KernelSU GKI mode"
    else
        log INFO "Extracting kernel zip"
    fi
    KERNEL_TMP="$(mktemp -d)"

    log DEBUG "Extracting kernel anykernel.sh installation script"
    unzip -j "$KERNEL_ZIP" anykernel.sh -d "$KERNEL_TMP"

    KERNEL_BOOT_FILE=$(unzip -l "$KERNEL_ZIP" | grep -oP 'Image.*' | head -n1)
    log DEBUG "Inferred kernel boot file: $KERNEL_BOOT_FILE"

    log DEBUG "Extracting kernel boot image"
    unzip -j "$KERNEL_ZIP" "$KERNEL_BOOT_FILE" -d "$KERNEL_TMP"

    KERNEL_BOOT="$KERNEL_TMP/$KERNEL_BOOT_FILE"
    log DEBUG "Kernel boot image: $KERNEL_BOOT"

    # check if dtbo.img is in $KERNEL_ZIP
    if unzip -l "$KERNEL_ZIP" dtbo.img &>/dev/null; then
        log DEBUG "Extracting kernel DTBO image"
        unzip -j "$KERNEL_ZIP" dtbo.img -d "$KERNEL_TMP"
        KERNEL_DTBO="$KERNEL_TMP/dtbo.img"
        log DEBUG "Kernel DTBO image: $KERNEL_DTBO"
    fi

    # check if vendor_kernel_boot is in anykernel.sh
    if grep -q vendor_kernel_boot "$KERNEL_TMP"/anykernel.sh; then
        log DEBUG "Kernel zip also requires patching vendor_kernel_boot block with the provided kernel boot image"
        KERNEL_VENDOR_KERNEL_BOOT=true
    fi
elif [[ "$KSU_MODE" = gki ]]; then
    log ERROR "Kernel zip not provided for KernelSU GKI mode"
    exit 1
fi

sign_image() {
    block="$1"
    target="$2"
    patch_image="$3"

    log INFO "Patching $block image"

    mkdir -p "$EXTRACTED/$block"
    pushd "$EXTRACTED/$block"
        log DEBUG "Unpacking $block image"
        avbroot avb unpack -i "../$block.img"

        # if KSU_BLOCK is not empty and KSU_BLOCK == block
        if [[ -n "$KSU_BLOCK" ]] && [[ $block = *boot* ]] && [[ "$KSU_BLOCK" == "$block" ]]; then
            log INFO "Patching KernelSU for $block raw image"
            ksud boot-patch \
                --boot raw.img \
                --module "$HOME/Downloads/android14-6.1_kernelsu.ko" \
                --kmi android14-6.1 \
                --out "$KSU_TMP"
            cp -v "$(realpath $KSU_TMP/kernelsu_patched_*.img)" raw.img
        fi

        if [[ $block = *boot* ]]; then
            log DEBUG "Unpacking $block boot image"
            avbroot boot unpack -i raw.img
        fi

        # if patch_image exists and target is not empty
        if [[ -f "$patch_image" ]] && [[ -n "$target" ]]; then
            log INFO "Replacing $block with $patch_image ($target target)"
            cp -v "$patch_image" "$target.img"
        fi

        if [[ $block = *boot* ]]; then
            log DEBUG "Repacking $block boot image"
            avbroot boot pack -o raw.img
        fi

        log INFO "Repacking and signing $block image"
        avbroot avb pack -o "../$block.modified.img" -k "$PRJ_ROOT/$DEVICE_PATH/avb.key" --pass-env-var PASS_AVB_ENV_VAR
    popd
}

if [[ -n "$KSU_BLOCK" ]]; then
    KSU_TMP="$(mktemp -d)"
    log DEBUG "KernelSU temporary directory: $KSU_TMP"
fi

if [[ -f "$KERNEL_BOOT" ]]; then
    if [[ $KSU_MODE = gki ]]; then
        log INFO "Patching GKI kernel with KernelSU AnyKernel3 zip"
        output_target="ksu_gki"
    else
        output_target="custom_kernel"
    fi

    sign_image boot kernel "$KERNEL_BOOT"
    ADDED_FLAGS=(--kernel-boot "$EXTRACTED/boot.modified.img")

    if [[ -n "$KERNEL_VENDOR_KERNEL_BOOT" ]] && [[ -f "$KERNEL_DTBO" ]]; then
        sign_image vendor_kernel_boot kernel "$KERNEL_BOOT"
        sign_image dtbo raw "$KERNEL_DTBO"
        ADDED_FLAGS+=(
            --kernel-vendor-kernel-boot "$EXTRACTED/vendor_kernel_boot.modified.img"
            --kernel-dtbo "$EXTRACTED/dtbo.modified.img"
        )
    elif [[ -n "$KERNEL_VENDOR_KERNEL_BOOT" ]]; then
        log WARN "Patching vendor_kernel_boot block implies patching dtbo image"
    elif [[ -f "$KERNEL_DTBO" ]]; then
        log WARN "Patching dtbo block usually means patching vendor_kernel_boot block"
        sign_image dtbo kernel "$KERNEL_DTBO"
    fi

    FLAGS+=("${ADDED_FLAGS[@]}")
    log DEBUG "Flags added:" "${ADDED_FLAGS[@]}"

    OUTPUT="$OUTPUT.$output_target"
    log DEBUG "Current expected OTA output: $OUTPUT"
elif [[ -v KERNEL_ZIP ]] || [[ -v KERNEL_BOOT ]]; then
    log WARN "Invalid kernel boot path ($KERNEL_BOOT), kernel zip ($KERNEL_ZIP)"
fi

if [[ -n "$KSU_BLOCK" ]] && [[ "$KSU_MODE" = lkm ]]; then
    # custom kernel (boot) has already been patched by KernelSU, so patch non-boot block instead (init_boot etc.)
    # stock kernel (*boot*) has already been patched by KernelSU, so patch boot block instead (boot/init_boot etc.)
    if [[ "$OUTPUT" == *custom_kernel* && "$KSU_BLOCK" != boot ]] || [[ "$OUTPUT" != *custom_kernel* && "$KSU_BLOCK" == *boot* ]]; then
        log INFO "Using KernelSU ($KSU_BLOCK)"
        sign_image "$KSU_BLOCK"
        ADDED_FLAGS=("--kernel-${KSU_BLOCK/_/-}" "$EXTRACTED/$KSU_BLOCK.modified.img")
        OUTPUT="$OUTPUT.ksu_lkm"
        FLAGS+=("${ADDED_FLAGS[@]}")
        log DEBUG "Flags added:" "${ADDED_FLAGS[@]}"
    fi
elif [[ -n "$KSU_BLOCK" ]] && [[ "$KSU_MODE" = gki ]]; then
    log WARN "KernelSU GKI mode is prioritized over patching block $KSU_BLOCK with KSU"
fi

declare -A module_path
for module in Custota MSD BCR OEMUnlockOnBoot AlterInstaller; do
    tag="$(curl -H "Authorization: token $GH_TOKEN" -s https://api.github.com/repos/chenxiaolong/$module/tags | jq -r '.[0].name')"
    log INFO "Latest release for $module is $tag."
    version="${tag/v/}"

    module_filename="$module-$version-release.zip"
    module_path[$module]="$MODULE_DIR/$module_filename"
    log DEBUG "Expect ${module_path[$module]}"

    if [[ ! -f "${module_path[$module]}" ]] || [[ ! -f "${module_path[$module]}.sig" ]]; then
        mkdir -p "$MODULE_DIR"
        pushd "$MODULE_DIR"
            module_url="https://github.com/chenxiaolong/$module/releases/download/${tag[$module]}/$module_filename"
            log INFO "Downloading $module_url to module dir"
            curl --remote-name -L "$module_url"
            curl --remote-name -L "$module_url.sig"
        popd
    fi
done

OUTPUT="$OUTPUT.zip"
mkdir -p "$OUTPUT_DIR"

log INFO "Start patching OTA"

set -x
python patch.py \
    --input "$INPUT" \
    --output "$OUTPUT" \
    --verify-public-key-avb "$EXTRACTED/avb_pkmd.bin" \
    --verify-cert-ota "$EXTRACTED/ota.crt" \
    --sign-key-avb "$DEVICE_PATH/avb.key" \
    --sign-key-ota "$DEVICE_PATH/ota.key" \
    --sign-cert-ota "$DEVICE_PATH/ota.crt" \
    --module-custota "${module_path[Custota]}" \
    --module-msd "${module_path[MSD]}" \
    --module-bcr "${module_path[BCR]}" \
    --module-oemunlockonboot "${module_path[OEMUnlockOnBoot]}" \
    --module-alterinstaller "${module_path[AlterInstaller]}" \
    --pass-avb-env-var PASS_AVB_ENV_VAR \
    --pass-ota-env-var PASS_OTA_ENV_VAR \
    "${FLAGS[@]}" && {
    set +x
    [[ -d "$KERNEL_TMP" ]] && rm -r "$KERNEL_TMP"
    [[ -d "$KSU_TMP" ]] && rm -r "$KSU_TMP"
    log INFO "Expect OTA file: $OUTPUT"
    exit 0
}

err_code=$?
[[ $err_code -ne 0 ]] && {
    log ERROR "An error occured while patching. See the logs above for more information."
    exit $err_code
}
