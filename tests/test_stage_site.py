import importlib.util
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/stage-site.py"
SPEC = importlib.util.spec_from_file_location("stage_site", SCRIPT)
SITE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SITE)


class StageSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.output = Path(self.temp.name) / "site"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.STDOUT)

    def file(self, name, contents=b"public fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def test_plan_is_read_only_and_selects_public_artifacts(self):
        allowed = [
            "releases.json", "linux/debian/gpg", "linux/debian/index.html",
            "linux/debian/dists/stable/InRelease", "linux/debian/dists/stable/Release.gpg",
            "linux/debian/dists/stable/main/binary-amd64/Packages.gz",
            "linux/ubuntu/pool/main/vmn_1.0.0_amd64.deb",
            "linux/arch/x86_64/vmn-1.0.0-1-x86_64.pkg.tar.zst.sig",
            "fdroid/index.html", "fdroid/repo/entry.jar", "fdroid/repo/App.apk",
            "fdroid/repo/icons/logo.png",
        ]
        for name in allowed:
            self.file(name)
        for name in ["README.md", ".env", ".github/workflows/deploy.yml", "tests/a.py",
                     "scripts/build.sh", "linux/debian/build/a.deb", "linux/arch/packages/staged/a.pkg.tar.zst",
                     "fdroid/metadata/app.yml", "fdroid/repo/.env", "fdroid/repo/secret.json"]:
            self.file(name, b"excluded fixture")
        self.commit()
        plan = SITE.plan(self.root)
        self.assertEqual(sorted(allowed), [f["path"] for f in plan["files"]])
        self.assertFalse(self.output.exists())

    def test_committed_bytes_not_modified_or_untracked_worktree(self):
        artifact = self.file("fdroid/repo/App.apk", b"original signed bytes")
        self.commit()
        artifact.write_bytes(b"uncommitted replacement")
        self.file("fdroid/repo/Untracked.apk", b"untracked fixture")
        self.file(".env", b"local excluded fixture")
        SITE.stage(self.root, self.output, SITE.plan(self.root))
        self.assertEqual(b"original signed bytes", (self.output / "fdroid/repo/App.apk").read_bytes())
        self.assertEqual(["fdroid/repo/App.apk"], [str(p.relative_to(self.output)) for p in self.output.rglob("*") if p.is_file()])

    def test_signed_metadata_is_preserved_exactly(self):
        names = ["linux/debian/dists/stable/InRelease", "linux/ubuntu/dists/stable/Release.gpg",
                 "fdroid/repo/index-v1.jar", "linux/arch/aarch64/app-1-1-aarch64.pkg.tar.zst.sig"]
        contents = b"\x00\xff\nopaque signature fixture\r\n"
        for name in names:
            self.file(name, contents)
        self.commit()
        SITE.stage(self.root, self.output, SITE.plan(self.root))
        for name in names:
            self.assertEqual(contents, (self.output / name).read_bytes())

    def test_arch_aliases_materialize_matching_committed_database(self):
        for suffix in ["db", "db.sig", "files", "files.sig"]:
            stem = suffix.removesuffix(".sig")
            signature = ".sig" if suffix.endswith(".sig") else ""
            target = f"repo.{stem}.tar.gz{signature}"
            self.file(f"linux/arch/x86_64/{target}", f"bytes:{suffix}".encode())
            (self.root / f"linux/arch/x86_64/repo.{suffix}").symlink_to(target)
        self.commit()
        # A working-tree redirect cannot change the committed alias or bytes.
        alias = self.root / "linux/arch/x86_64/repo.db"
        alias.unlink()
        alias.symlink_to("/etc/passwd")
        SITE.stage(self.root, self.output, SITE.plan(self.root))
        for suffix in ["db", "db.sig", "files", "files.sig"]:
            staged = self.output / f"linux/arch/x86_64/repo.{suffix}"
            self.assertFalse(staged.is_symlink())
            self.assertEqual(f"bytes:{suffix}".encode(), staged.read_bytes())

    def test_arch_alias_rejects_escape_wrong_database_and_missing_target(self):
        for target in ["/etc/passwd", "../../../../secret", "other.db.tar.gz", "repo.files.tar.gz", "repo.db.tar.gz", "repo.db.tar.gz\n"]:
            with self.subTest(target=target):
                alias = self.root / "linux/arch/x86_64/repo.db"
                alias.parent.mkdir(parents=True, exist_ok=True)
                if alias.is_symlink():
                    alias.unlink()
                alias.symlink_to(target)
                self.commit()
                with self.assertRaisesRegex(ValueError, "Unsafe or missing"):
                    SITE.plan(self.root)

    def test_arch_alias_rejects_chained_symlink(self):
        self.file("releases.json")
        alias = self.root / "linux/arch/x86_64/repo.db"
        alias.parent.mkdir(parents=True)
        alias.symlink_to("repo.db.tar.gz")
        (alias.parent / "repo.db.tar.gz").symlink_to("/etc/passwd")
        self.commit()
        with self.assertRaises(ValueError):
            SITE.plan(self.root)

    def test_other_public_symlinks_rejected(self):
        for name in ["releases.json", "fdroid/repo/App.apk", "linux/debian/pool/main/a.deb"]:
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to("/etc/passwd")
                self.commit()
                with self.assertRaisesRegex(ValueError, "Unapproved public symlink"):
                    SITE.plan(self.root)
                path.unlink()

    def test_path_allowlist_rejects_traversal_hidden_and_control_files(self):
        for name in ["/releases.json", "../releases.json", "fdroid/repo/../repo/App.apk",
                     "fdroid//repo/App.apk", "linux/debian/dists/.env/Release", "linux/arch/x86_64/.key.db",
                     "linux/debian/pool/main/private.key", "fdroid/repo/index.py", "fdroid/metadata/app.yml"]:
            with self.subTest(name=name):
                self.assertFalse(SITE.public_path(name))

    def test_empty_public_payload_fails(self):
        self.file("README.md")
        self.commit()
        with self.assertRaisesRegex(ValueError, "No committed public artifacts"):
            SITE.plan(self.root)

    def test_staging_refuses_existing_output_and_output_inside_checkout(self):
        self.file("releases.json")
        self.commit()
        plan = SITE.plan(self.root)
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            SITE.stage(self.root, self.output, plan)
        with self.assertRaisesRegex(ValueError, "outside"):
            SITE.stage(self.root, self.root / "build/site", plan)
        redirect = Path(self.temp.name) / "redirect"
        redirect.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "outside"):
            SITE.stage(self.root, redirect / "site", plan)

    def test_source_must_be_repository_root(self):
        self.file("releases.json")
        self.commit()
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "repository root"):
            SITE.plan(nested)

    def test_cli_defaults_to_dry_run_requires_write_and_fresh_output(self):
        self.file("releases.json", b"fixture")
        self.commit()
        command = [sys.executable, str(SCRIPT), "--source", str(self.root)]
        result = subprocess.run(command + ["--output", str(self.output)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('"mode": "dry-run"', result.stdout)
        self.assertFalse(self.output.exists())
        result = subprocess.run(command + ["--write"], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        result = subprocess.run(command + ["--output", str(self.output), "--write"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"fixture", (self.output / "releases.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
