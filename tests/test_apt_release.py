import hashlib
import gzip
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AptReleaseTests(unittest.TestCase):
    def test_release_hashes_only_indexes_with_relative_paths(self):
        for distro in ('debian', 'ubuntu'):
            with self.subTest(distro=distro), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stable = root / 'dists/stable'
                indexes = {}
                for arch in ('amd64', 'arm64'):
                    name = f'main/binary-{arch}/Packages'
                    path = stable / name
                    path.parent.mkdir(parents=True)
                    path.write_bytes(f'Package: example-{arch}\n'.encode())
                    indexes[name] = path.read_bytes()
                for name in ('Release', 'Release.gpg', 'InRelease'):
                    (stable / name).write_bytes(b'old metadata must not be hashed')
                script = ROOT / f'linux/{distro}/scripts/generate.release.sh'
                output = subprocess.check_output(['bash', str(script)], cwd=root, text=True)
                self.assertIn('Architectures: amd64 arm64\n', output)
                section = None
                seen = {}
                algorithms = {'MD5Sum': 'md5', 'SHA1': 'sha1', 'SHA256': 'sha256', 'SHA512': 'sha512'}
                for line in output.splitlines():
                    if line.endswith(':') and line[:-1] in algorithms:
                        section = line[:-1]
                        seen[section] = []
                    elif line.startswith('  '):
                        digest, size, path = line.split()
                        self.assertIn(path, indexes)
                        self.assertEqual(int(size), len(indexes[path]))
                        self.assertEqual(digest, hashlib.new(algorithms[section], indexes[path]).hexdigest())
                        seen[section].append(path)
                for section in algorithms:
                    self.assertEqual(sorted(seen[section]), sorted(indexes))

    def test_scan_indexes_both_architectures_and_retains_versions(self):
        for distro in ('debian', 'ubuntu'):
            with self.subTest(distro=distro), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pool = root / 'pool/main'
                pool.mkdir(parents=True)
                for version, arch in [('1.0.0', 'amd64'), ('2.0.0', 'amd64'), ('2.0.0', 'arm64')]:
                    tree = root / f'fixture-{version}-{arch}'
                    (tree / 'DEBIAN').mkdir(parents=True)
                    (tree / 'DEBIAN/control').write_text(
                        f'Package: example\nVersion: {version}\nArchitecture: {arch}\n'
                        'Maintainer: Fixture <fixture@example.invalid>\nDescription: Test package\n')
                    subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(tree),
                                    str(pool / f'example_{version}_{arch}.deb')],
                                   check=True, capture_output=True)
                script = ROOT / f'linux/{distro}/scripts/generate.scan.sh'
                subprocess.run(['bash', str(script)], cwd=root, check=True, capture_output=True)
                for arch in ('amd64', 'arm64'):
                    index = root / f'dists/stable/main/binary-{arch}/Packages'
                    text = index.read_text()
                    self.assertIn('Version: 2.0.0\n', text)
                    self.assertIn(f'Architecture: {arch}\n', text)
                    if arch == 'amd64':
                        self.assertIn('Version: 1.0.0\n', text)
                        self.assertEqual(text.count('Package: example\n'), 2)
                    else:
                        self.assertEqual(text.count('Package: example\n'), 1)
                    self.assertEqual(gzip.decompress(index.with_suffix('.gz').read_bytes()), index.read_bytes())


if __name__ == '__main__':
    unittest.main()
