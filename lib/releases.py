# SPDX-FileCopyrightText: 2026 Daniel Phan
# SPDX-License-Identifier: GPL-3.0-only

import dataclasses
import json
import logging
from pathlib import Path
import re
import shutil
import urllib.error
import urllib.request


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ModuleRelease:
    repo: str
    filename_prefix: str


OFFICIAL_MODULES: dict[str, ModuleRelease] = {
    'alterinstaller': ModuleRelease('AlterInstaller', 'AlterInstaller'),
    'bcr': ModuleRelease('BCR', 'BCR'),
    'custota': ModuleRelease('Custota', 'Custota'),
    'msd': ModuleRelease('MSD', 'MSD'),
    'oemunlockonboot': ModuleRelease('OEMUnlockOnBoot', 'OEMUnlockOnBoot'),
}


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'my-avbroot-setup',
    }

    if token:
        headers['Authorization'] = f'Bearer {token}'

    return headers


def _open(url: str, token: str | None):
    request = urllib.request.Request(url, headers=_headers(token))
    return urllib.request.urlopen(request)


def latest_tag(module: str, token: str | None = None) -> str:
    release = OFFICIAL_MODULES[module]
    url = f'https://api.github.com/repos/chenxiaolong/{release.repo}/tags'

    logger.info(f'Fetching latest release tag: {release.repo}')

    with _open(url, token) as response:
        tags = json.load(response)

    if not tags:
        raise ValueError(f'No tags found for chenxiaolong/{release.repo}')

    return tags[0]['name']


def module_filename(module: str, tag: str) -> str:
    release = OFFICIAL_MODULES[module]
    version = tag.removeprefix('v')
    return f'{release.filename_prefix}-{version}-release.zip'


def module_release_url(module: str, tag: str) -> str:
    release = OFFICIAL_MODULES[module]
    filename = module_filename(module, tag)
    return (
        f'https://github.com/chenxiaolong/{release.repo}/releases/download/'
        f'{tag}/{filename}'
    )


def _download(url: str, output: Path, token: str | None = None) -> None:
    logger.info(f'Downloading {url} to {output}')

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f'{output.name}.tmp')

    try:
        with (
            _open(url, token) as response,
            temp.open('wb') as f_out,
        ):
            shutil.copyfileobj(response, f_out)

        temp.replace(output)
    finally:
        if temp.exists():
            temp.unlink()


def _version_key(version: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r'([0-9]+)', version)
    )


def cached_official_module(
    module: str,
    module_dir: Path,
) -> tuple[Path, Path] | None:
    release = OFFICIAL_MODULES[module]
    pattern = f'{release.filename_prefix}-*-release.zip'
    matches: list[tuple[tuple[object, ...], Path, Path]] = []

    for zip_path in module_dir.glob(pattern):
        sig_path = Path(f'{zip_path}.sig')
        if not sig_path.is_file():
            continue

        version = zip_path.name.removeprefix(
            f'{release.filename_prefix}-').removesuffix('-release.zip')
        matches.append((_version_key(version), zip_path, sig_path))

    if not matches:
        return None

    _, zip_path, sig_path = max(matches, key=lambda item: item[0])
    return zip_path, sig_path


def ensure_official_module(
    module: str,
    module_dir: Path,
    token: str | None = None,
) -> tuple[Path, Path]:
    try:
        tag = latest_tag(module, token)
        logger.info(f'Latest release for {OFFICIAL_MODULES[module].repo} is {tag}')

        filename = module_filename(module, tag)
        zip_path = module_dir / filename
        sig_path = Path(f'{zip_path}.sig')

        if not zip_path.is_file():
            _download(module_release_url(module, tag), zip_path, token)

        if not sig_path.is_file():
            _download(f'{module_release_url(module, tag)}.sig', sig_path, token)

        return zip_path, sig_path
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        cached = cached_official_module(module, module_dir)
        if cached is None:
            raise

        zip_path, sig_path = cached
        logger.warning(
            f'Failed to fetch latest {OFFICIAL_MODULES[module].repo} release '
            f'({e}); using cached module {zip_path.name}')
        return zip_path, sig_path
