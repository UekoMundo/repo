"""Offline stdlib tests: fixture archives, mocked downloads, observable packaging calls."""

import contextlib
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_tools", ROOT / "scripts/package-tools.py")
tools = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tools
SPEC.loader.exec_module(tools)


def archive_bytes(name="vmn", kind=tarfile.REGTYPE, extra=None):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o7777  # untrusted release modes are never preserved
            member.linkname = "../../outside"
            data = b"fixture release binary\n"
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
            if extra:
                archive.addfile(tarfile.TarInfo(extra), io.BytesIO())
    return buffer.getvalue()


def release(version="0.3.5", name="vmn", arch="amd64", payload=None):
    payload = archive_bytes(name) if payload is None else payload
    tag = f"{name}-v{version}"
    asset = f"{name}-linux-{arch}.tar.gz"
    return {"version": version, "sourceTag": "v" + version, "tag": tag,
            "prerelease": False, "assets": [{"name": asset,
            "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
            "urls": {"repo": f"https://github.com/UekoMundo/repo/releases/download/{tools.parse.quote(tag, safe='')}/{asset}",
                     "homebrewTap": "https://github.com/UekoMundo/homebrew-tap/unused"}}]}


def manifest(releases=None, name="vmn"):
    return {"schemaVersion": 1, "packages": [{"name": name,
            "sourceRepository": "UekoMundo/Kenkon", "sourcePath": "tools/" + name,
            "releases": [release(name=name)] if releases is None else releases}]}


def archive_with_appledouble(metadata, before=True, metadata_kind=tarfile.REGTYPE):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        entries = [("._vmn", metadata, metadata_kind), ("vmn", b"fixture binary", tarfile.REGTYPE)]
        for name, payload, kind in (entries if before else reversed(entries)):
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(payload) if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(payload) if member.size else None)
    return buffer.getvalue()


class SelectionTests(unittest.TestCase):
    def test_semver_numeric_sort_and_prereleases(self):
        prerelease = release("2.0.0")
        prerelease["prerelease"] = True
        selected = tools.select_release(manifest([release("0.9.9"), release("0.10.0"),
                                                  release("9.0.0-rc.1"), prerelease]), "vmn", "amd64")
        self.assertEqual(selected.version, "0.10.0")
        self.assertEqual(selected.tag, "vmn-v0.10.0")

    def test_select_latest_stable_with_required_arch(self):
        selected = tools.select_release(manifest([release("1.1.0", arch="arm64"),
                                                  release("1.0.0")]), "vmn", "amd64")
        self.assertEqual(selected.version, "1.0.0")
        selected = tools.select_release(manifest([release("1.1.0", arch="arm64")]), "vmn", "arm64")
        self.assertEqual(selected.asset, "vmn-linux-arm64.tar.gz")

    def test_fresh_rust_release_platform_aliases(self):
        for name in tools.PACKAGES:
            for arch, selection_arch in (("x86_64", "amd64"), ("aarch64", "arm64")):
                with self.subTest(name=name, arch=arch):
                    selected = tools.select_release(manifest([release("1.0.0", name, arch)], name), name, selection_arch)
                    self.assertEqual(selected.asset, f"{name}-linux-{arch}.tar.gz")

    def test_version_prefix_and_build_metadata(self):
        selected = tools.select_release(manifest([release("v1.2.3+build.5")]), "vmn", "amd64")
        self.assertEqual(selected.version, "1.2.3+build.5")
        self.assertEqual(tools.package_filename(selected, "deb", "amd64"), "vmn_1.2.3+build.5_amd64.deb")
        self.assertEqual(tools.package_filename(selected, "arch", "aarch64"),
                         "vmn-1.2.3_build.5-1-aarch64.pkg.tar.zst")

    def test_legacy_vmp_linux_x86_64(self):
        selected = tools.select_release(manifest([release("0.1.2", "vmp", "x86_64")], "vmp"), "vmp", "amd64")
        self.assertEqual(selected.asset, "vmp-linux-x86_64.tar.gz")
        with self.assertRaisesRegex(tools.PackageError, "No stable public Linux arm64"):
            tools.select_release(manifest([release("0.1.2", "vmp", "x86_64")], "vmp"), "vmp", "arm64")

    def test_missing_packages_and_releases_are_clear(self):
        for name in ("relay", "vault-sync"):
            with self.subTest(name=name), self.assertRaisesRegex(tools.PackageError, "publish and mirror"):
                tools.select_release(manifest([], name), name, "amd64")
        with self.assertRaisesRegex(tools.PackageError, "exactly one package"):
            tools.select_release(manifest(), "relay", "amd64")

    def test_invalid_digest_and_size(self):
        for field, values in (("sha256", [None, "0" * 64, "z" * 64, "abc"]),
                              ("size", [None, True, 0, -1, tools.MAX_ARCHIVE_SIZE + 1])):
            for value in values:
                data = manifest()
                data["packages"][0]["releases"][0]["assets"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(tools.PackageError):
                    tools.select_release(data, "vmn", "amd64")

    def test_bad_schema_and_duplicate_assets(self):
        data = manifest()
        data["schemaVersion"] = 2
        with self.assertRaisesRegex(tools.PackageError, "schemaVersion"):
            tools.select_release(data, "vmn", "amd64")
        data = manifest()
        assets = data["packages"][0]["releases"][0]["assets"]
        assets.append(copy.deepcopy(assets[0]))
        with self.assertRaisesRegex(tools.PackageError, "Duplicate"):
            tools.select_release(data, "vmn", "amd64")

    def test_url_trust_boundary(self):
        good = release()["assets"][0]["urls"]["repo"]
        bad_urls = [good.replace("https:", "http:"), good.replace("github.com", "github.com.evil.test"),
                    good.replace("github.com", "github.com@evil.test"), good + "?download=1", good + "#fragment",
                    good.replace("UekoMundo/repo", "vineelsai26/VMN"), good.replace("UekoMundo/repo", "UekoMundo/homebrew-tap"),
                    good.replace("github.com", "github.com:443"), good.replace("vmn-v0.3.5/", "../vmn-v0.3.5/"),
                    good.replace("vmn-linux", "wrong-linux"), good.replace("vmn-v0.3.5/", "other-tag/")]
        for url in bad_urls:
            data = manifest()
            data["packages"][0]["releases"][0]["assets"][0]["urls"]["repo"] = url
            with self.subTest(url=url), self.assertRaisesRegex(tools.PackageError, "exact public"):
                tools.select_release(data, "vmn", "amd64")

    def test_unsafe_tag(self):
        for tag in ("../../evil", "/absolute", "tag/../evil", "tag\\evil", "tag\ncommand"):
            with self.subTest(tag=tag), self.assertRaises(tools.PackageError):
                tools.safe_release_url(tag, "vmn-linux-amd64.tar.gz", "unused")

    def test_redirect_trust_boundary(self):
        tools.validate_redirect("https://release-assets.githubusercontent.com/github-production-release-asset/1/file?sig=abc")
        for url in ("http://release-assets.githubusercontent.com/file", "https://evil.test/file",
                    "https://127.0.0.1/file", "https://github.com@evil.test/file", "https://github.com:444/file"):
            with self.subTest(url=url), self.assertRaises(tools.PackageError):
                tools.validate_redirect(url)
        with self.assertRaises(tools.PackageError):
            tools.ReleaseRedirectHandler().redirect_request(
                None, None, 302, "", {}, "https://evil.test/redirect")


class ArchiveTests(unittest.TestCase):
    def test_historical_appledouble_metadata_is_validated_and_discarded(self):
        metadata = b"\x00\x05\x16\x07\x00\x02\x00\x00" + bytes(16) + (1).to_bytes(2, "big")
        metadata += (9).to_bytes(4, "big") + (38).to_bytes(4, "big") + (4).to_bytes(4, "big") + b"info"
        for before in (True, False):
            with self.subTest(before=before), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "archive.tar.gz").write_bytes(archive_with_appledouble(metadata, before))
                tools.extract_binary(path / "archive.tar.gz", "vmn", path / "vmn")
                self.assertEqual((path / "vmn").read_bytes(), b"fixture binary")
                self.assertFalse((path / "._vmn").exists())

    def test_invalid_appledouble_metadata_and_links_fail_before_output(self):
        metadata = b"\x00\x05\x16\x07\x00\x02\x00\x00" + bytes(16) + (1).to_bytes(2, "big")
        metadata += (9).to_bytes(4, "big") + (38).to_bytes(4, "big") + (99).to_bytes(4, "big") + b"info"
        for payload, kind in ((metadata, tarfile.REGTYPE), (b"x" * 42, tarfile.REGTYPE), (metadata, tarfile.SYMTYPE)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "archive.tar.gz").write_bytes(archive_with_appledouble(payload, metadata_kind=kind))
                with self.assertRaisesRegex(tools.PackageError, "AppleDouble"):
                    tools.extract_binary(path / "archive.tar.gz", "vmn", path / "vmn")
                self.assertFalse((path / "vmn").exists())

    def test_bounded_historical_pax_metadata_and_unsafe_overrides(self):
        for headers, allowed in (({"mtime": "1.5", "SCHILY.xattr.com.apple.provenance": "example"}, True), ({"path": "../vmn"}, False), ({"size": "999999999"}, False)):
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                with tarfile.open(path / "archive.tar.gz", "w:gz", format=tarfile.PAX_FORMAT) as archive:
                    member = tarfile.TarInfo("vmn")
                    member.pax_headers = headers
                    member.size = 6
                    archive.addfile(member, io.BytesIO(b"binary"))
                if allowed:
                    tools.extract_binary(path / "archive.tar.gz", "vmn", path / "vmn")
                    self.assertEqual((path / "vmn").read_bytes(), b"binary")
                else:
                    with self.assertRaisesRegex(tools.PackageError, "Unsafe PAX"):
                        tools.extract_binary(path / "archive.tar.gz", "vmn", path / "vmn")
                    self.assertFalse((path / "vmn").exists())

    def test_hidden_pax_and_gnu_extensions_are_rejected_before_output(self):
        for format in (tarfile.PAX_FORMAT, tarfile.GNU_FORMAT):
            with self.subTest(format=format), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                with tarfile.open(path / "archive.tar.gz", "w:gz", format=format) as archive:
                    member = tarfile.TarInfo("vmn" if format == tarfile.PAX_FORMAT else "x" * 1048576)
                    if format == tarfile.PAX_FORMAT:
                        member.pax_headers = {"comment": "x" * 1048576}
                    member.size = 6
                    archive.addfile(member, io.BytesIO(b"binary"))
                with self.assertRaisesRegex(tools.PackageError, "extra entries"):
                    tools.extract_binary(path / "archive.tar.gz", "vmn", path / "vmn")
                self.assertFalse((path / "vmn").exists())

    def test_safe_binary_and_permissions(self):
        for name in ("vmn", "./vmn"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "archive").write_bytes(archive_bytes(name))
                tools.extract_binary(path / "archive", "vmn", path / "binary")
                self.assertEqual((path / "binary").read_bytes(), b"fixture release binary\n")
                self.assertEqual((path / "binary").stat().st_mode & 0o7777, 0o755)

    def test_unsafe_members_are_rejected_without_output(self):
        invalid = [("../vmn", tarfile.REGTYPE), ("/vmn", tarfile.REGTYPE),
                   ("subdir/vmn", tarfile.REGTYPE), ("other", tarfile.REGTYPE),
                   ("vmn", tarfile.SYMTYPE), ("vmn", tarfile.LNKTYPE),
                   ("vmn", tarfile.DIRTYPE), ("vmn", tarfile.FIFOTYPE),
                   ("vmn", tarfile.CHRTYPE)]
        for name, kind in invalid:
            with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "archive").write_bytes(archive_bytes(name, kind))
                with self.assertRaises(tools.PackageError):
                    tools.extract_binary(path / "archive", "vmn", path / "binary")
                self.assertFalse((path / "binary").exists())

    def test_extra_and_duplicate_members_are_rejected(self):
        for extra in ("vmn", "README", "../outside", "/absolute", "dir/"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "archive").write_bytes(archive_bytes(extra=extra))
                with self.assertRaisesRegex(tools.PackageError, "extra entries"):
                    tools.extract_binary(path / "archive", "vmn", path / "binary")
                self.assertFalse((path / "binary").exists())

    def test_empty_and_corrupt_archive(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz"):
            pass
        for payload in (buffer.getvalue(), b"not a gzip tar archive"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "archive").write_bytes(payload)
                with self.assertRaises((tools.PackageError, tarfile.TarError)):
                    tools.extract_binary(path / "archive", "vmn", path / "binary")

    def test_download_digest_and_size(self):
        payload = archive_bytes()
        selection = tools.select_release(manifest([release(payload=payload)]), "vmn", "amd64")
        for downloaded in (payload, payload[:-1], payload + b"extra", b"x" * len(payload)):
            with self.subTest(valid=downloaded == payload), tempfile.TemporaryDirectory() as directory:
                opener = mock.Mock()
                opener.open.return_value = io.BytesIO(downloaded)
                with mock.patch.object(tools.request, "build_opener", return_value=opener):
                    if downloaded == payload:
                        tools.download_archive(selection, Path(directory) / "archive")
                    else:
                        with self.assertRaises(tools.PackageError):
                            tools.download_archive(selection, Path(directory) / "archive")
                opener.open.assert_called_once_with(selection.url, timeout=60)


class CliAndPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve()
        self.manifest = self.path / "releases.json"
        self.manifest.write_text(json.dumps(manifest()), encoding="utf-8")
        self.args = ["--manifest", str(self.manifest), "--package", "vmn", "--format", "deb",
                     "--arch", "amd64", "--output", str(self.path / "output")]

    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = tools.main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_default_and_explicit_dry_run_are_observable_and_have_no_effects(self):
        for mode in ([], ["--dry-run"]):
            with mock.patch.object(tools.request, "build_opener") as network, \
                    mock.patch.object(tools.subprocess, "run") as packager:
                status, stdout, stderr = self.invoke(self.args + mode)
                self.assertEqual(status, 0, stderr)
                plan = json.loads(stdout)
                self.assertEqual(plan["mode"], "dry-run")
                self.assertEqual(plan["version"], "0.3.5")
                self.assertEqual(plan["installPath"], "/usr/bin/vmn")
                self.assertEqual(plan["output"], str(self.path / "output/vmn_0.3.5_amd64.deb"))
                network.assert_not_called()
                packager.assert_not_called()
                self.assertFalse((self.path / "output").exists())

    def test_environment_manifest_default(self):
        with mock.patch.dict(os.environ, {"RELEASE_MANIFEST": str(self.manifest)}):
            status, stdout, stderr = self.invoke(self.args[2:])
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["version"], "0.3.5")

    def test_missing_manifest_and_missing_tool(self):
        self.manifest.unlink()
        status, _, stderr = self.invoke(self.args)
        self.assertEqual(status, 1)
        self.assertIn("releases.json", stderr)
        self.manifest.write_text(json.dumps(manifest()))
        with mock.patch.object(tools.shutil, "which", return_value=None):
            status, _, stderr = self.invoke(self.args + ["--build"])
        self.assertEqual(status, 1)
        self.assertIn("dpkg-deb is required", stderr)

    def test_protected_output_and_existing_history(self):
        selection = tools.select_release(manifest(), "vmn", "amd64")
        name = tools.package_filename(selection, "deb", "amd64")
        for folder in (ROOT / "linux/debian/pool/main", ROOT / "linux/ubuntu/dists/stable",
                       ROOT / "linux/arch/x86_64", ROOT / "linux/arch/aarch64"):
            with self.subTest(folder=folder), self.assertRaisesRegex(tools.PackageError, "unsigned output"):
                tools.validate_output(folder, name)
        with mock.patch.object(tools.Path, "cwd", return_value=self.path):
            with self.assertRaisesRegex(tools.PackageError, "unsigned output"):
                tools.validate_output(self.path / "pool/main", name)
        (self.path / "output").mkdir()
        historical = self.path / "output" / name
        historical.write_bytes(b"existing historical package")
        status, _, stderr = self.invoke(self.args + ["--build"])
        self.assertEqual(status, 1)
        self.assertIn("overwrite", stderr)
        self.assertEqual(historical.read_bytes(), b"existing historical package")
        historical.unlink()
        historical.symlink_to(self.path / "missing")
        with self.assertRaisesRegex(tools.PackageError, "overwrite"):
            tools.validate_output(self.path / "output", name)

    def test_symlinked_output_cannot_bypass_protected_pool(self):
        (self.path / "pool-link").symlink_to(ROOT / "linux/debian/pool/main")
        with self.assertRaisesRegex(tools.PackageError, "unsigned output"):
            tools.validate_output(self.path / "pool-link", "new.deb")

    def fake_network(self, payload=None):
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(archive_bytes() if payload is None else payload)
        return mock.patch.object(tools.request, "build_opener", return_value=opener)

    def test_build_deb_verifies_and_installs_usr_bin(self):
        def package(command, **kwargs):
            self.assertEqual(command[:3], ["dpkg-deb", "--build", "--root-owner-group"])
            tree = Path(command[3])
            control = (tree / "DEBIAN/control").read_text()
            self.assertIn("Version: 0.3.5\n", control)
            self.assertIn("Architecture: amd64\n", control)
            for directory in (tree, tree / "usr", tree / "usr/bin", tree / "DEBIAN"):
                self.assertEqual(directory.stat().st_mode & 0o7777, 0o755)
            self.assertEqual((tree / "DEBIAN/control").stat().st_mode & 0o7777, 0o644)
            self.assertEqual((tree / "usr/bin/vmn").read_bytes(), b"fixture release binary\n")
            self.assertFalse((tree / "usr/local/bin").exists())
            self.assertEqual((tree / "usr/bin/vmn").stat().st_mode & 0o7777, 0o755)
            Path(command[4]).write_bytes(b"fixture deb")
        with self.fake_network(), mock.patch.object(tools.shutil, "which", return_value="fake-dpkg"), \
                mock.patch.object(tools.subprocess, "run", side_effect=package) as packager:
            old_umask = os.umask(0o077)
            try:
                status, stdout, stderr = self.invoke(self.args + ["--build"])
            finally:
                os.umask(old_umask)
        self.assertEqual(status, 0, stderr)
        packager.assert_called_once()
        self.assertIn("Staged unsigned package", stdout)
        staged = self.path / "output/vmn_0.3.5_amd64.deb"
        self.assertEqual(staged.read_bytes(), b"fixture deb")
        self.assertEqual(staged.stat().st_mode & 0o7777, 0o644)

    def test_vault_sync_declares_native_dbus_runtime(self):
        selected = tools.select_release(manifest([release("0.1.0", "vault-sync")], "vault-sync"), "vault-sync", "amd64")
        work = self.path / "dbus-work"
        work.mkdir()
        binary = self.path / "vault-sync"
        binary.write_bytes(b"fixture binary")
        with mock.patch.object(tools.subprocess, "run"):
            tools.build_deb(selected, "amd64", binary, work, self.path / "fixture.deb")
        self.assertIn("Depends: libdbus-1-3\n", (work / "package/DEBIAN/control").read_text())
        self.assertIn("depends=('dbus')", (ROOT / "linux/arch/templates/vault-sync.PKGBUILD").read_text())

    def test_build_arch_uses_local_verified_template(self):
        def package(command, cwd, **kwargs):
            self.assertEqual(command[0], "makepkg")
            self.assertNotIn("--force", command)
            self.assertNotIn("--sign", command)
            self.assertIn("--nosign", command)
            text = (cwd / "PKGBUILD").read_text()
            self.assertIn("pkgver='0.3.5'", text)
            self.assertIn("arch=('aarch64')", text)
            self.assertIn('"$pkgdir/usr/bin/vmn"', text)
            self.assertIn(hashlib.sha256(b"fixture release binary\n").hexdigest(), text)
            self.assertNotIn("git", text.split("source=")[1])
            self.assertIn("CARCH=aarch64", (cwd / "makepkg.conf").read_text())
            (cwd / "vmn-0.3.5-1-aarch64.pkg.tar.zst").write_bytes(b"fixture arch")
        self.manifest.write_text(json.dumps(manifest([release(arch="arm64")])))
        args = self.args.copy()
        args[args.index("deb")] = "arch"
        args[args.index("amd64")] = "arm64"
        with self.fake_network(), mock.patch.object(tools.shutil, "which", return_value="fake-makepkg"), \
                mock.patch.object(tools.subprocess, "run", side_effect=package) as packager:
            status, _, stderr = self.invoke(args + ["--build"])
        self.assertEqual(status, 0, stderr)
        packager.assert_called_once()
        self.assertEqual((self.path / "output/vmn-0.3.5-1-aarch64.pkg.tar.zst").read_bytes(), b"fixture arch")

    def test_verification_failure_never_calls_packager(self):
        payload = archive_bytes()
        with self.fake_network(b"x" * len(payload)), mock.patch.object(tools.shutil, "which", return_value="fake"), \
                mock.patch.object(tools.subprocess, "run") as packager:
            status, _, stderr = self.invoke(self.args + ["--build"])
        self.assertEqual(status, 1)
        self.assertIn("SHA256 mismatch", stderr)
        packager.assert_not_called()
        self.assertFalse((self.path / "output").exists())

    def test_unsafe_archive_never_calls_packager(self):
        payload = archive_bytes("../vmn")
        self.manifest.write_text(json.dumps(manifest([release(payload=payload)])))
        with self.fake_network(payload), mock.patch.object(tools.shutil, "which", return_value="fake"), \
                mock.patch.object(tools.subprocess, "run") as packager:
            status, _, stderr = self.invoke(self.args + ["--build"])
        self.assertEqual(status, 1)
        self.assertIn("expected regular binary", stderr)
        packager.assert_not_called()
        self.assertFalse((self.path / "output").exists())

    def test_packager_failure_does_not_publish_partial_output(self):
        with self.fake_network(), mock.patch.object(tools.shutil, "which", return_value="fake"), \
                mock.patch.object(tools.subprocess, "run", side_effect=subprocess.CalledProcessError(2, "fake")):
            status, _, _ = self.invoke(self.args + ["--build"])
        self.assertEqual(status, 1)
        self.assertFalse((self.path / "output").exists())

    def test_all_local_arch_templates_are_valid_bash(self):
        for name in tools.PACKAGES:
            template = ROOT / "linux/arch/templates" / (name + ".PKGBUILD")
            text = tools.string.Template(template.read_text()).substitute(
                VERSION="1.2.3", ARCH="x86_64", BINARY_SHA256="a" * 64)
            result = subprocess.run(["bash", "-n"], input=text, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'"$pkgdir/usr/bin/{name}"', text)
            self.assertNotIn("SKIP", text)

    def test_docker_bind_mount_layout_can_find_shared_helper_and_manifest(self):
        shared = self.path / "opt/release-repo"
        (shared / "scripts").mkdir(parents=True)
        tools.shutil.copyfile(ROOT / "scripts/package-tools.py", shared / "scripts/package-tools.py")
        tools.shutil.copyfile(self.manifest, shared / "releases.json")
        env = dict(os.environ, PACKAGE_TOOLS=str(shared / "scripts/package-tools.py"),
                   RELEASE_MANIFEST=str(shared / "releases.json"), PYTHONDONTWRITEBYTECODE="1")
        for distro in ("debian", "ubuntu", "arch"):
            mount = self.path / distro / "home/build/repo"
            (mount / "scripts").mkdir(parents=True)
            source = ROOT / "linux" / distro / "scripts"
            script = "build.sh" if distro == "arch" else "vmn.build.sh"
            tools.shutil.copyfile(source / script, mount / "scripts" / script)
            if distro == "arch":
                env["PACKAGES"] = "vmn"
            result = subprocess.run(["bash", str(mount / "scripts" / script), "--arch", "amd64"],
                                    cwd=mount, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["mode"], "dry-run")
            self.assertTrue(plan["output"].startswith(str(mount)))
            self.assertFalse((mount / "build").exists())
            self.assertFalse((mount / "packages").exists())

    def test_all_wrappers_and_make_defaults_are_offline_dry_runs(self):
        entries = [manifest(name=name)["packages"][0] for name in tools.PACKAGES]
        self.manifest.write_text(json.dumps({"schemaVersion": 1, "packages": entries}))
        env = dict(os.environ, RELEASE_MANIFEST=str(self.manifest),
                   PACKAGE_TOOLS=str(ROOT / "scripts/package-tools.py"), PYTHONDONTWRITEBYTECODE="1")
        for distro in ("debian", "ubuntu"):
            for name in tools.PACKAGES:
                result = subprocess.run(["bash", str(ROOT / "linux" / distro / "scripts" / (name + ".build.sh")),
                                         "--arch", "amd64", "--output", str(self.path / "output")],
                                        cwd=self.path, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["mode"], "dry-run")
            result = subprocess.run(["make", "-C", str(ROOT / "linux" / distro), "generate",
                                     "PACKAGE_ARGS=--arch amd64"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('"mode": "dry-run"'), 4)
        result = subprocess.run(["make", "-C", str(ROOT / "linux/arch"), "build",
                                 "PACKAGE_ARGS=--arch amd64"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('"mode": "dry-run"'), 4)
        self.assertFalse((self.path / "output").exists())


if __name__ == "__main__":
    unittest.main()
