import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'linux/arch/scripts/release.sh'


class ArchReleaseTests(unittest.TestCase):
    def test_preview_is_read_only_and_write_preserves_history(self):
        for arch in ('x86_64', 'aarch64'):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pool = root / arch
                pool.mkdir()
                package = pool / f'example-2.0.0-1-{arch}.pkg.tar.zst'
                historical = pool / f'example-1.0.0-1-{arch}.pkg.tar.xz'
                database = pool / 'vineelsai-arch-repo.db.tar.gz'
                for path in (package, historical, database):
                    path.write_bytes(b'preserve these bytes')
                commands = root / 'commands.json'
                executable = root / 'repo-add'
                executable.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                                      'open(os.environ["COMMANDS"],"w").write(json.dumps(sys.argv[1:]))\n')
                executable.chmod(0o755)
                env = {**os.environ, 'CPU_ARCH': arch, 'PATH': str(root)+os.pathsep+os.environ['PATH'], 'COMMANDS': str(commands)}
                result = subprocess.run(['bash', str(SCRIPT)], cwd=root, env=env, check=True, capture_output=True, text=True)
                self.assertIn('Planned signed database update', result.stdout)
                self.assertFalse(commands.exists(), 'preview never invokes the publisher')
                subprocess.run(['bash', str(SCRIPT), '--write'], cwd=root, env=env, check=True, capture_output=True)
                for path in (package, historical, database):
                    self.assertEqual(path.read_bytes(), b'preserve these bytes')
                args = json.loads(commands.read_text())
                self.assertIn('--prevent-downgrade', args)
                self.assertIn('--include-sigs', args)
                self.assertIn('--sign', args)
                self.assertIn(package.name, args)
                self.assertIn(historical.name, args)

    def test_unknown_modes_and_architectures_fail(self):
        for args, arch in [(['--unknown'], 'x86_64'), ([], '../outside')]:
            result = subprocess.run(['bash', str(SCRIPT), *args], env={**os.environ, 'CPU_ARCH': arch}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
