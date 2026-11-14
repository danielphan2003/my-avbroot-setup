# SPDX-FileCopyrightText: 2026 Daniel Phan
# SPDX-License-Identifier: GPL-3.0-only

import dataclasses
import logging
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import zipfile

from lib import external


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class KernelOptions:
    input_ota: Path
    extracted_dir: Path
    signing_key: external.SigningKey
    kernel_zip: Path | None
    kernel_boot: Path | None
    kernel_dtbo: Path | None
    ksu_block: str | None
    ksu_mode: str | None
    ksu_module: Path
    ksu_kmi: str


@dataclasses.dataclass
class PreparedKernelImages:
    replacements: dict[str, Path]
    suffixes: list[str]


@dataclasses.dataclass
class _KernelZip:
    boot: Path
    dtbo: Path | None
    patch_vendor_kernel_boot: bool


def ensure_partitions(
    input_ota: Path,
    output_dir: Path,
    partitions: set[str],
) -> None:
    missing = sorted(
        partition for partition in partitions
        if not (output_dir / f'{partition}.img').is_file()
    )

    if not missing:
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    external.unpack_ota(input_ota, output_dir, missing)


def _extract_zip_member(
    z: zipfile.ZipFile,
    member: str,
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)

    with (
        z.open(member, 'r') as f_in,
        output.open('wb') as f_out,
    ):
        shutil.copyfileobj(f_in, f_out)


def _extract_kernel_zip(kernel_zip: Path, output_dir: Path) -> _KernelZip:
    logger.info(f'Extracting kernel zip: {kernel_zip}')

    with zipfile.ZipFile(kernel_zip, 'r') as z:
        names = z.namelist()

        anykernel = next(
            (n for n in names if PurePosixPath(n).name == 'anykernel.sh'),
            None,
        )
        if anykernel is None:
            raise ValueError(f'Missing anykernel.sh in {kernel_zip}')

        kernel_image = next(
            (n for n in names if PurePosixPath(n).name.startswith('Image')),
            None,
        )
        if kernel_image is None:
            raise ValueError(f'Missing Image* kernel file in {kernel_zip}')

        anykernel_path = output_dir / 'anykernel.sh'
        boot_path = output_dir / PurePosixPath(kernel_image).name

        _extract_zip_member(z, anykernel, anykernel_path)
        _extract_zip_member(z, kernel_image, boot_path)

        dtbo_member = next(
            (n for n in names if PurePosixPath(n).name == 'dtbo.img'),
            None,
        )
        if dtbo_member is None:
            dtbo_path = None
        else:
            dtbo_path = output_dir / 'dtbo.img'
            _extract_zip_member(z, dtbo_member, dtbo_path)

        patch_vendor_kernel_boot = \
            'vendor_kernel_boot' in anykernel_path.read_text()

    return _KernelZip(
        boot=boot_path,
        dtbo=dtbo_path,
        patch_vendor_kernel_boot=patch_vendor_kernel_boot,
    )


def _partition_flag(partition: str) -> str:
    return f"--kernel-{partition.replace('_', '-')}"


def _patch_kernelsu(
    raw_image: Path,
    output_dir: Path,
    module: Path,
    kmi: str,
) -> None:
    if not module.is_file():
        raise FileNotFoundError(module)

    logger.info(f'Patching KernelSU into raw boot image: {raw_image}')

    subprocess.check_call([
        'ksud',
        'boot-patch',
        '--boot', raw_image,
        '--module', module,
        '--kmi', kmi,
        '--out', output_dir,
    ])

    patched = next(output_dir.glob('kernelsu_patched_*.img'), None)
    if patched is None:
        raise FileNotFoundError('KernelSU did not create a patched image')

    shutil.copyfile(patched, raw_image)


def _patch_and_sign_partition(
    options: KernelOptions,
    partition: str,
    target: str | None = None,
    replacement: Path | None = None,
) -> Path:
    ensure_partitions(options.input_ota, options.extracted_dir, {partition})

    source = options.extracted_dir / f'{partition}.img'
    modified = options.extracted_dir / f'{partition}.modified.img'
    work_dir = options.extracted_dir / partition

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    logger.info(f'Patching {partition} image')
    external.unpack_avb(source, work_dir)

    raw_image = work_dir / 'raw.img'

    if (
        options.ksu_block == partition
        and 'boot' in partition
        and options.ksu_mode == 'lkm'
    ):
        with tempfile.TemporaryDirectory(
            prefix='ksu-',
            dir=options.extracted_dir,
        ) as ksu_temp:
            _patch_kernelsu(
                raw_image,
                Path(ksu_temp),
                options.ksu_module,
                options.ksu_kmi,
            )

    if 'boot' in partition:
        external.unpack_boot(raw_image, work_dir)

    if replacement is not None and target is not None:
        if not replacement.is_file():
            raise FileNotFoundError(replacement)

        logger.info(f'Replacing {partition} {target}.img with {replacement}')
        shutil.copyfile(replacement, work_dir / f'{target}.img')

    if 'boot' in partition:
        external.pack_boot(raw_image, work_dir)

    external.pack_avb(modified, work_dir, options.signing_key, False)
    return modified


def prepare_kernel_images(options: KernelOptions) -> PreparedKernelImages:
    replacements: dict[str, Path] = {}
    suffixes: list[str] = []
    kernel_boot = options.kernel_boot
    kernel_dtbo = options.kernel_dtbo
    patch_vendor_kernel_boot = False

    options.extracted_dir.mkdir(parents=True, exist_ok=True)

    if options.kernel_zip is not None and options.kernel_zip.is_file():
        with tempfile.TemporaryDirectory(
            prefix='kernel-',
            dir=options.extracted_dir,
        ) as kernel_temp:
            kernel_zip = _extract_kernel_zip(options.kernel_zip, Path(kernel_temp))
            kernel_boot = kernel_zip.boot
            kernel_dtbo = kernel_zip.dtbo or kernel_dtbo
            patch_vendor_kernel_boot = kernel_zip.patch_vendor_kernel_boot

            _prepare_kernel_boot_replacements(
                options,
                kernel_boot,
                kernel_dtbo,
                patch_vendor_kernel_boot,
                replacements,
                suffixes,
            )
    elif options.ksu_mode == 'gki':
        raise ValueError('Kernel zip must be provided for KernelSU GKI mode')
    else:
        _prepare_kernel_boot_replacements(
            options,
            kernel_boot,
            kernel_dtbo,
            patch_vendor_kernel_boot,
            replacements,
            suffixes,
        )

    if options.ksu_block and options.ksu_mode == 'lkm':
        has_custom_kernel = 'custom_kernel' in suffixes

        if (
            has_custom_kernel and options.ksu_block != 'boot'
            or not has_custom_kernel and 'boot' in options.ksu_block
        ):
            logger.info(f'Using KernelSU LKM block: {options.ksu_block}')
            replacements[options.ksu_block] = _patch_and_sign_partition(
                options,
                options.ksu_block,
            )
            suffixes.append('ksu_lkm')
    elif options.ksu_block and options.ksu_mode == 'gki':
        logger.warning(
            f'KernelSU GKI mode is prioritized over '
            f'patching {options.ksu_block} with KernelSU')

    for partition in replacements:
        logger.debug(f'{_partition_flag(partition)} {replacements[partition]}')

    return PreparedKernelImages(
        replacements=replacements,
        suffixes=suffixes,
    )


def _prepare_kernel_boot_replacements(
    options: KernelOptions,
    kernel_boot: Path | None,
    kernel_dtbo: Path | None,
    patch_vendor_kernel_boot: bool,
    replacements: dict[str, Path],
    suffixes: list[str],
) -> None:
    if kernel_boot is not None and kernel_boot.is_file():
        output_target = 'ksu_gki' if options.ksu_mode == 'gki' else 'custom_kernel'

        replacements['boot'] = _patch_and_sign_partition(
            options,
            'boot',
            'kernel',
            kernel_boot,
        )

        if patch_vendor_kernel_boot and kernel_dtbo is not None:
            replacements['vendor_kernel_boot'] = _patch_and_sign_partition(
                options,
                'vendor_kernel_boot',
                'kernel',
                kernel_boot,
            )
            replacements['dtbo'] = _patch_and_sign_partition(
                options,
                'dtbo',
                'raw',
                kernel_dtbo,
            )
        elif patch_vendor_kernel_boot:
            logger.warning('Patching vendor_kernel_boot implies patching dtbo')
        elif kernel_dtbo is not None and kernel_dtbo.is_file():
            logger.warning(
                'Patching dtbo usually means patching vendor_kernel_boot')
            replacements['dtbo'] = _patch_and_sign_partition(
                options,
                'dtbo',
                'raw',
                kernel_dtbo,
            )

        suffixes.append(output_target)
    elif kernel_dtbo is not None and kernel_dtbo.is_file():
        replacements['dtbo'] = _patch_and_sign_partition(
            options,
            'dtbo',
            'raw',
            kernel_dtbo,
        )
        suffixes.append('custom_kernel')
    elif kernel_boot is not None or options.kernel_zip is not None:
        logger.warning(
            f'Invalid kernel boot path ({kernel_boot}), '
            f'kernel zip ({options.kernel_zip})')
