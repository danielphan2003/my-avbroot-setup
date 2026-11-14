#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Daniel Phan
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import datetime
import hashlib
import logging
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import zipfile

from lib import external, kernel, releases


logger = logging.getLogger(__name__)
TCE_MODULE = 'tce'


def _valid_module_names() -> set[str]:
    return {*releases.OFFICIAL_MODULES, TCE_MODULE}


def _input_basename(input_ota: Path) -> str:
    name = input_ota.name
    return name.removesuffix('.zip')


def build_output_path(
    input_ota: Path,
    output_dir: Path,
    suffixes: list[str],
    now: datetime.datetime | None = None,
) -> Path:
    if now is None:
        now = datetime.datetime.now()

    timestamp = now.strftime('%Y-%m-%d_%H-%M-%S')
    pieces = [_input_basename(input_ota), timestamp, *suffixes]
    filename = '.'.join(pieces)
    return output_dir / f'{filename}.zip'


def _add_module_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--modules',
        help='Comma-separated modules to include '
             f'({", ".join(sorted(_valid_module_names()))})',
    )

    for name in releases.OFFICIAL_MODULES:
        parser.add_argument(
            f'--module-{name}',
            type=Path,
            help=f'{name} module zip',
        )
        parser.add_argument(
            f'--module-{name}-sig',
            type=Path,
            help=f'{name} module zip signature',
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--device',
        required=True,
        help='Device name',
    )
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
        '--device-dir',
        type=Path,
        help='Directory containing per-device signing keys',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        help='Output directory',
    )
    parser.add_argument(
        '--module-dir',
        type=Path,
        help='Directory for downloaded official modules',
    )
    parser.add_argument(
        '--extract-dir',
        type=Path,
        help='Directory for extracted OTA files',
    )
    parser.add_argument(
        '--module-tce',
        type=Path,
        help='TCE module zip',
    )
    parser.add_argument(
        '--kernel-zip',
        type=Path,
        help='AnyKernel3 zip containing a custom kernel',
    )
    parser.add_argument(
        '--kernel-boot',
        type=Path,
        help='Custom boot kernel image',
    )
    parser.add_argument(
        '--kernel-dtbo',
        type=Path,
        help='Custom dtbo image',
    )
    parser.add_argument(
        '--ksu-block',
        help='Boot partition to patch with KernelSU',
    )
    parser.add_argument(
        '--ksu-mode',
        choices=['gki', 'lkm'],
        help='KernelSU patch mode',
    )
    parser.add_argument(
        '--ksu-module',
        type=Path,
        default=Path.home() / 'Downloads' / 'android14-6.1_kernelsu.ko',
        help='KernelSU LKM module',
    )
    parser.add_argument(
        '--ksu-kmi',
        default='android14-6.1',
        help='KernelSU KMI string',
    )
    parser.add_argument(
        '--magisk',
        type=Path,
        help='Magisk APK to patch boot image',
    )
    parser.add_argument(
        '--magisk-preinit-device',
        help='Magisk preinit device',
    )
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose logging',
    )
    parser.add_argument(
        '--debug-shell',
        action='store_true',
        help='Spawn a patch.py debug shell before cleanup',
    )
    parser.add_argument(
        '--skip-custota-tool',
        action='store_true',
        help='Skip creating Custota csig file and update JSON file',
    )
    parser.add_argument(
        '--patch-arg',
        action='append',
        help='Extra argument to pass to `avbroot ota patch`',
    )
    _add_module_args(parser)

    args = parser.parse_args()
    args.modules = _parse_modules(parser, args.modules)
    return args


def _parse_modules(
    parser: argparse.ArgumentParser,
    modules: str | None,
) -> set[str] | None:
    if modules is None:
        return None

    selected = {
        module.strip()
        for module in modules.split(',')
        if module.strip()
    }
    valid = _valid_module_names()
    invalid = sorted(selected - valid)

    if invalid:
        parser.error(
            f'invalid module(s): {", ".join(invalid)} '
            f'(valid: {", ".join(sorted(valid))})')

    return selected


def _default_path(path: Path | None, fallback: Path) -> Path:
    return path if path is not None else fallback


def _require_env(name: str) -> None:
    if not os.getenv(name):
        raise ValueError(f'Please set {name}')


def _extract_ota_cert(input_ota: Path, output: Path) -> None:
    logger.info(f'Extracting OTA certificate: {output}')

    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(input_ota, 'r') as z,
        z.open('META-INF/com/android/otacert', 'r') as f_in,
        output.open('wb') as f_out,
    ):
        f_out.write(f_in.read())


def _extract_avb_public_key(input_ota: Path, extracted_dir: Path, output: Path):
    logger.info(f'Extracting public AVB key: {output}')

    kernel.ensure_partitions(input_ota, extracted_dir, {'vbmeta'})

    info = subprocess.check_output([
        'avbroot',
        'avb',
        'info',
        '--input',
        extracted_dir / 'vbmeta.img',
    ], text=True)

    match = re.search(r'public_key: "([^"]+)"', info)
    if match is None:
        raise ValueError('Unable to find public_key in vbmeta AVB info')

    public_key = re.sub(r'\s+', '', match.group(1))
    output.write_bytes(bytes.fromhex(public_key))


def ensure_verification_material(
    input_ota: Path,
    extracted_dir: Path,
) -> tuple[Path, Path]:
    extracted_dir.mkdir(parents=True, exist_ok=True)

    avb_pkmd = extracted_dir / 'avb_pkmd.bin'
    ota_cert = extracted_dir / 'ota.crt'

    if not avb_pkmd.is_file():
        _extract_avb_public_key(input_ota, extracted_dir, avb_pkmd)

    digest = hashlib.sha256(avb_pkmd.read_bytes()).hexdigest()
    logger.info(f'SHA256 of public AVB key from original OTA: {digest}')

    if not ota_cert.is_file():
        _extract_ota_cert(input_ota, ota_cert)

    return avb_pkmd, ota_cert


def _official_module_paths(
    args: argparse.Namespace,
    module_dir: Path,
) -> dict[str, tuple[Path, Path]]:
    result: dict[str, tuple[Path, Path]] = {}
    token = os.getenv('GH_TOKEN')

    for name in releases.OFFICIAL_MODULES:
        if args.modules is not None and name not in args.modules:
            continue

        zip_path: Path | None = getattr(args, f'module_{name}')
        sig_path: Path | None = getattr(args, f'module_{name}_sig')

        if zip_path is None:
            zip_path, sig_path = releases.ensure_official_module(
                name,
                module_dir,
                token,
            )
        elif sig_path is None:
            sig_path = Path(f'{zip_path}.sig')

        result[name] = (zip_path, sig_path)

    return result


def _include_tce(args: argparse.Namespace) -> bool:
    return (
        args.module_tce is not None
        and (args.modules is None or TCE_MODULE in args.modules)
    )


def _validate_module_selection(args: argparse.Namespace) -> None:
    if args.modules is None:
        return

    if TCE_MODULE in args.modules and args.module_tce is None:
        raise ValueError('--modules includes tce, but --module-tce is missing')


def _magisk_args(args: argparse.Namespace, suffixes: list[str]) -> list[str]:
    if (
        args.magisk is not None
        and args.magisk.is_file()
        and args.magisk_preinit_device
    ):
        suffixes.append('magisk')
        return [
            '--magisk',
            str(args.magisk),
            '--magisk-preinit-device',
            args.magisk_preinit_device,
        ]

    if args.magisk is not None or args.magisk_preinit_device is not None:
        logger.warning(
            f'Invalid magisk ({args.magisk}), '
            f'preinit device ({args.magisk_preinit_device})')

    return []


def _patch_command(
    repo_root: Path,
    args: argparse.Namespace,
    output: Path,
    device_path: Path,
    avb_pkmd: Path,
    ota_cert: Path,
    module_paths: dict[str, tuple[Path, Path]],
    kernel_replacements: dict[str, Path],
    magisk_args: list[str],
) -> list[str]:
    cmd = [
        sys.executable,
        str(repo_root / 'patch.py'),
        '--input', str(args.input),
        '--output', str(output),
        '--verify-public-key-avb', str(avb_pkmd),
        '--verify-cert-ota', str(ota_cert),
        '--sign-key-avb', str(device_path / 'avb.key'),
        '--sign-key-ota', str(device_path / 'ota.key'),
        '--sign-cert-ota', str(device_path / 'ota.crt'),
        '--pass-avb-env-var', 'PASS_AVB_ENV_VAR',
        '--pass-ota-env-var', 'PASS_OTA_ENV_VAR',
    ]

    for name, (zip_path, sig_path) in module_paths.items():
        cmd.extend([
            f'--module-{name}',
            str(zip_path),
            f'--module-{name}-sig',
            str(sig_path),
        ])

    if _include_tce(args):
        cmd.extend(['--module-tce', str(args.module_tce)])

    for partition, image in kernel_replacements.items():
        cmd.extend([
            f"--kernel-{partition.replace('_', '-')}",
            str(image),
        ])

    cmd.extend(magisk_args)

    if args.debug_shell:
        cmd.append('--debug-shell')

    if args.skip_custota_tool:
        cmd.append('--skip-custota-tool')

    for patch_arg in args.patch_arg or []:
        cmd.extend(['--patch-arg', patch_arg])

    return cmd


def run(args: argparse.Namespace) -> None:
    _require_env('PASS_AVB_ENV_VAR')
    _require_env('PASS_OTA_ENV_VAR')
    _validate_module_selection(args)

    repo_root = Path(__file__).resolve().parent
    device_dir = _default_path(args.device_dir, repo_root / 'device')
    output_dir = _default_path(args.output_dir, repo_root / 'ota' / args.device)
    module_dir = _default_path(args.module_dir, repo_root / 'module')
    extract_dir = _default_path(args.extract_dir, repo_root / 'extracted')
    extracted = extract_dir / _input_basename(args.input)
    device_path = device_dir / args.device

    os.environ.setdefault('TMPDIR', str(extract_dir))

    if not args.input.is_file():
        raise FileNotFoundError(args.input)

    avb_pkmd, ota_cert = ensure_verification_material(args.input, extracted)
    module_paths = _official_module_paths(args, module_dir)

    suffixes: list[str] = []
    magisk_args = _magisk_args(args, suffixes)

    kernel_result = kernel.prepare_kernel_images(kernel.KernelOptions(
        input_ota=args.input,
        extracted_dir=extracted,
        signing_key=external.SigningKey(
            device_path / 'avb.key',
            'PASS_AVB_ENV_VAR',
            None,
        ),
        kernel_zip=args.kernel_zip,
        kernel_boot=args.kernel_boot,
        kernel_dtbo=args.kernel_dtbo,
        ksu_block=args.ksu_block,
        ksu_mode=args.ksu_mode,
        ksu_module=args.ksu_module,
        ksu_kmi=args.ksu_kmi,
    ))
    suffixes.extend(kernel_result.suffixes)

    output = args.output
    if output is None:
        output = build_output_path(args.input, output_dir, suffixes)

    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = _patch_command(
        repo_root,
        args,
        output,
        device_path,
        avb_pkmd,
        ota_cert,
        module_paths,
        kernel_result.replacements,
        magisk_args,
    )

    logger.info(f'Running: {shlex.join(cmd)}')
    subprocess.check_call(cmd)
    logger.info(f'Expect OTA file: {output}')


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='\x1b[1m[%(levelname)s] %(message)s\x1b[0m',
    )

    try:
        run(args)
    except Exception:
        logger.exception('Failed to make OTA')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
