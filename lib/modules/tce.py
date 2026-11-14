# SPDX-FileCopyrightText: 2026 Daniel Phan
# SPDX-License-Identifier: GPL-3.0-only

from collections.abc import Iterable
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import override
import zipfile

from lib import modules
from lib.filesystem import CpioFs, ExtFs
from lib.initscript import InitScript
from lib.linux import linux_android_abi, linux_run
from lib.modules import Module, ModuleRequirements


logger = logging.getLogger(__name__)


class TCEModule(Module):
    def __init__(self, zip: Path) -> None:
        super().__init__()

        self.zip: Path = zip
        self.abi: str = linux_android_abi()

    @override
    def requirements(self) -> ModuleRequirements:
        return ModuleRequirements(
            boot_images=set(),
            ext_images={'system'},
            selinux_patching=True,
        )

    @override
    def inject(
        self,
        boot_fs: dict[str, CpioFs],
        ext_fs: dict[str, ExtFs],
        sepolicies: Iterable[Path],
    ) -> None:
        logger.info(f'Injecting TCE: {self.zip}')

        system_fs = ext_fs['system']

        with (
            zipfile.ZipFile(self.zip, 'r') as z,
            tempfile.NamedTemporaryFile(delete_on_close=False) as f_temp,
        ):
            for path in z.namelist():
                if path == f'tce-tool.{self.abi}':
                    with z.open(path) as f_exe:
                        shutil.copyfileobj(f_exe, f_temp)
                    os.fchmod(f_temp.fileno(), 0o700)
                    f_temp.close()

                if path == 'tce-tool.arm64-v8a':
                    dest_path = 'system/bin/tce-tool'
                    perms = 0o755
                elif path.endswith('.apk'):
                    dest_path = path
                    perms = 0o644
                else:
                    continue

                modules.zip_extract(z, path, system_fs, mode=perms, output=dest_path)

            assert f_temp.closed

            for sepolicy in sepolicies:
                logger.info(f'Adding TCE SELinux rules: {sepolicy}')

                linux_run(
                    [
                        f_temp.name,
                        'sepatch',
                        '--source', sepolicy,
                        '--target', sepolicy,
                    ],
                    inputs=[f_temp.name, sepolicy],
                    outputs=[sepolicy],
                )

            seapp = 'system/etc/selinux/plat_seapp_contexts'
            logger.info(f'Adding TCE seapp context: {seapp}')

            with (
                z.open('plat_seapp_contexts', 'r') as f_in,
                system_fs.open(seapp, 'ab') as f_out,
            ):
                shutil.copyfileobj(f_in, f_out)
                f_out.write(b'\n')

        InitScript(
            name='tce_init',
            command=[
                '/system/bin/tce-tool',
                'nfc-init',
                '--log-target', 'logcat',
                '--log-level', 'debug',
            ],
            class_='main',
            user='root',
            seclabel='u:r:tce_init:s0',
            capabilities=['CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SYS_ADMIN'],
            condition='post-fs-data',
            blocking=True,
        ).add_to(system_fs)

        InitScript(
            name='tce_daemon',
            command=[
                '/system/bin/tce-tool',
                'daemon',
                '--log-target', 'logcat',
                '--log-level', 'debug',
            ],
            class_='main',
            user='system',
            group='system',
            seclabel='u:r:tce_daemon:s0',
            capabilities=['DAC_OVERRIDE', 'FOWNER', 'KILL', 'SYS_PTRACE'],
        ).add_to(system_fs)
