#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024-2026 Andrew Gunnerson
# SPDX-FileCopyrightText: 2026 Daniel Phan
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import dataclasses
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import tomlkit

from lib import external, filesystem, modules
from lib.filesystem import CpioFs, CpioInfo, ExtFs, ExtInfo


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BootImagePaths:
    image: Path
    unpacked: Path
    raw_image: Path
    ramdisk: Path
    metadata: Path
    tree: Path

    def __init__(self, images_dir: Path, unpacked_dir: Path, name: str) -> None:
        self.image = images_dir / f'{name}.img'
        self.unpacked = unpacked_dir / name
        self.raw_image = self.unpacked / 'raw.img'
        self.ramdisk = self.unpacked / 'ramdisk.img.0'
        self.metadata = self.unpacked / 'cpio.toml'
        self.tree = self.unpacked / 'cpio_tree'


@dataclasses.dataclass
class ExtImagePaths:
    image: Path
    unpacked: Path
    raw_image: Path
    metadata: Path
    tree: Path

    def __init__(self, images_dir: Path, unpacked_dir: Path, name: str) -> None:
        self.image = images_dir / f'{name}.img'
        self.unpacked = unpacked_dir / name
        self.raw_image = self.unpacked / 'raw.img'
        self.metadata = self.unpacked / 'fs_metadata.toml'
        self.tree = self.unpacked / 'fs_tree'


def get_ota_metadata(ota: Path) -> dict[str, str]:
    props: dict[str, str] = {}

    with zipfile.ZipFile(ota, 'r') as z:
        with z.open('META-INF/com/android/metadata', 'r') as f:
            for line in f:
                line = line.decode('UTF-8').strip()

                key, delim, value = line.partition('=')
                if not delim:
                    raise ValueError(f'Bad OTA metadata line: {line!r}')

                props[key] = value

    return props


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--input',
        type=Path,
        required=True,
        help='Input OTA',
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='Output OTA',
    )
    parser.add_argument(
        '--verify-public-key-avb',
        type=Path,
        help='AVB public key for verifying input OTA',
    )
    parser.add_argument(
        '--verify-cert-ota',
        type=Path,
        help='OTA certificate for verifying input OTA',
    )
    parser.add_argument(
        '--sign-key-avb',
        type=Path,
        required=True,
        help='AVB private key for signing output OTA',
    )
    parser.add_argument(
        '--sign-key-ota',
        type=Path,
        required=True,
        help='OTA private key for signing output OTA',
    )
    parser.add_argument(
        '--sign-cert-ota',
        type=Path,
        required=True,
        help='OTA certificate for signing output OTA',
    )
    parser.add_argument(
        '--kernel-boot',
        type=Path,
        help='Custom boot image to include',
    )
    parser.add_argument(
        '--kernel-init-boot',
        type=Path,
        help='Custom init_boot image to include',
    )
    parser.add_argument(
        '--kernel-vendor-kernel-boot',
        type=Path,
        help='Custom vendor_kernel_boot image to include',
    )
    parser.add_argument(
        '--kernel-dtbo',
        type=Path,
        help='Custom dtbo image to include',
    )
    parser.add_argument(
        '--magisk',
        type=Path,
        help='Magisk APK to patch boot image',
    )
    parser.add_argument(
        '--magisk-preinit-device',
        type=str,
        help='Magisk preinit device',
    )
    parser.add_argument(
        '--debug-shell',
        action='store_true',
        help='Spawn a debug shell before cleaning up temporary directory',
    )
    parser.add_argument(
        '--pass-avb-env-var',
        type=str,
        help='Private key passphrase environment variable for AVB signing',
    )
    parser.add_argument(
        '--pass-ota-env-var',
        type=str,
        help='Private key passphrase environment variable for OTA signing',
    )
    parser.add_argument(
        '--pass-avb-file',
        type=Path,
        help='Private key passphrase file for AVB signing',
    )
    parser.add_argument(
        '--pass-ota-file',
        type=Path,
        help='Private key passphrase file for OTA signing',
    )
    parser.add_argument(
        '--patch-arg',
        action='append',
        help='Extra argument to pass to `avbroot ota patch`',
    )
    parser.add_argument(
        '--skip-custota-tool',
        action='store_true',
        help='Skip creating Custota csig file and update JSON file',
    )

    for name, spec in modules.all_modules().items():
        parser.add_argument(
            f'--module-{name}',
            type=Path,
            help=f'{name} module zip',
        )

        if spec.signature_required:
            parser.add_argument(
                f'--module-{name}-sig',
                type=Path,
                help=f'{name} module zip signature',
            )

    args = parser.parse_args()

    if args.output is None:
        args.output = Path(f'{args.input}.patched')

    for name, spec in modules.all_modules().items():
        zip_path: Path | None = getattr(args, f'module_{name}')

        if zip_path is None or not spec.signature_required:
            continue

        sig_key = f'module_{name}_sig'
        if getattr(args, sig_key) is None:
            setattr(args, sig_key, Path(f'{zip_path}.sig'))

    return args


def _patch_args(args: argparse.Namespace) -> list[str]:
    extra_args = list(args.patch_arg or [])

    if args.magisk is not None or args.magisk_preinit_device is not None:
        if args.magisk is None or args.magisk_preinit_device is None:
            raise ValueError(
                '--magisk and --magisk-preinit-device must be provided together')

        extra_args.extend([
            '--magisk',
            str(args.magisk),
            '--magisk-preinit-device',
            args.magisk_preinit_device,
        ])
    elif not extra_args:
        extra_args.append('--rootless')

    return extra_args


def _kernel_replacements(args: argparse.Namespace) -> dict[str, Path]:
    replacements: dict[str, Path] = {}

    if args.kernel_boot:
        replacements['boot'] = args.kernel_boot
    if args.kernel_init_boot:
        replacements['init_boot'] = args.kernel_init_boot
    if args.kernel_vendor_kernel_boot:
        replacements['vendor_kernel_boot'] = args.kernel_vendor_kernel_boot
    if args.kernel_dtbo:
        replacements['dtbo'] = args.kernel_dtbo

    return replacements


def run(args: argparse.Namespace, temp_dir: Path) -> None:
    sign_key_avb = external.SigningKey(
        args.sign_key_avb,
        args.pass_avb_env_var,
        args.pass_avb_file,
    )
    sign_key_ota = external.SigningKey(
        args.sign_key_ota,
        args.pass_ota_env_var,
        args.pass_ota_file,
    )

    inject_modules: list[modules.Module] = []
    need_boot_fs: set[str] = set()
    need_ext_fs: set[str] = set()
    need_sepolicies = False

    for name, spec in modules.all_modules().items():
        zip_path: Path | None = getattr(args, f'module_{name}')

        if zip_path is None:
            continue

        if spec.signature_required:
            sig_path: Path = getattr(args, f'module_{name}_sig')
            module = spec.constructor(zip_path, sig_path)
        else:
            module = spec.constructor(zip_path)

        inject_modules.append(module)

        requirements = module.requirements()
        need_boot_fs |= requirements.boot_images
        need_ext_fs |= requirements.ext_images
        need_sepolicies |= requirements.selinux_patching

    if need_ext_fs:
        need_ext_fs.add('system')

    if need_sepolicies:
        need_boot_fs.add('vendor_boot')
        need_ext_fs.add('vendor')

    external.verify_ota(
        args.input,
        args.verify_public_key_avb,
        args.verify_cert_ota,
    )

    images_dir = temp_dir / 'images'
    if need_boot_fs or need_ext_fs:
        external.unpack_ota(args.input, images_dir, need_boot_fs | need_ext_fs)

    boot_fs: dict[str, CpioFs] = {}
    for name in sorted(need_boot_fs):
        paths = BootImagePaths(images_dir, temp_dir, name)

        paths.unpacked.mkdir()
        external.unpack_avb(paths.image, paths.unpacked)
        external.unpack_boot(paths.raw_image, paths.unpacked)
        external.unpack_cpio(paths.ramdisk, paths.unpacked)

        with open(paths.metadata, 'rb') as f:
            info = CpioInfo.model_validate(tomlkit.load(f))

        boot_fs[name] = CpioFs(info=info, tree=paths.tree)

    ext_fs: dict[str, ExtFs] = {}
    for name in sorted(need_ext_fs):
        paths = ExtImagePaths(images_dir, temp_dir, name)

        paths.unpacked.mkdir()
        external.unpack_avb(paths.image, paths.unpacked)
        external.unpack_fs(paths.raw_image, paths.unpacked)

        with open(paths.metadata, 'rb') as f:
            info = ExtInfo.model_validate(tomlkit.load(f))

        ext_fs[name] = ExtFs(info=info, tree=paths.tree, contexts=[])

    if ext_fs:
        contexts = filesystem.load_file_contexts(
            ext_fs['system'].tree /
            'system' / 'etc' / 'selinux' / 'plat_file_contexts',
        )

        for fs in ext_fs.values():
            fs.contexts = contexts

    if need_sepolicies:
        selinux_policies = [
            boot_fs['vendor_boot'].tree / 'sepolicy',
            ext_fs['vendor'].tree / 'etc' / 'selinux' / 'precompiled_sepolicy',
        ]
    else:
        selinux_policies = []

    for module in inject_modules:
        module.inject(boot_fs, ext_fs, selinux_policies)

    for name, fs in ext_fs.items():
        paths = ExtImagePaths(images_dir, temp_dir, name)

        with open(paths.metadata, 'w') as f:
            tomlkit.dump(fs.info.model_dump(exclude_none=True), f)

        external.pack_fs(paths.raw_image, paths.unpacked)
        external.pack_avb(paths.image, paths.unpacked, sign_key_avb, True)

    for name, fs in boot_fs.items():
        paths = BootImagePaths(images_dir, temp_dir, name)

        with open(paths.metadata, 'w') as f:
            tomlkit.dump(fs.info.model_dump(exclude_none=True), f)

        external.pack_cpio(paths.ramdisk, paths.unpacked)
        external.pack_boot(paths.raw_image, paths.unpacked)
        external.pack_avb(paths.image, paths.unpacked, sign_key_avb, False)

    modified_replacements = {
        name: images_dir / f'{name}.img'
        for name in boot_fs.keys() | ext_fs.keys()
    }

    external.patch_ota(
        args.input,
        args.output,
        sign_key_avb,
        sign_key_ota,
        args.sign_cert_ota,
        modified_replacements | _kernel_replacements(args),
        _patch_args(args),
    )

    if not args.skip_custota_tool:
        external.generate_csig(args.output, sign_key_ota, args.sign_cert_ota)

        codename = get_ota_metadata(args.output)['pre-device']
        update_info = args.output.parent / f'{codename}.json'
        external.generate_update_info(update_info, args.output.name)


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format='\x1b[1m[%(levelname)s] %(message)s\x1b[0m',
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        exit_code = 0

        try:
            run(args, Path(temp_dir))
        except Exception:
            logger.exception('Failed to patch OTA')
            exit_code = 1

        if args.debug_shell:
            shell = os.getenv('SHELL', 'bash')
            logger.info(f'Debug shell: {shell}')
            subprocess.run([shell], cwd=temp_dir)

        raise SystemExit(exit_code)


if __name__ == '__main__':
    main()
