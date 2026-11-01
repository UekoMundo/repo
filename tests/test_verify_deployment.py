import importlib.util
import io
from pathlib import Path
from unittest.mock import patch
import unittest

import test_stage_site as fixtures


SPEC = importlib.util.spec_from_file_location("verify_deployment", Path(__file__).resolve().parents[1] / "scripts/verify-deployment.py")
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class VerifyDeploymentTests(unittest.TestCase):
    setUp = fixtures.StageSiteTests.setUp
    git = fixtures.StageSiteTests.git
    file = fixtures.StageSiteTests.file
    commit = fixtures.StageSiteTests.commit

    def test_live_bytes_match_committed_files_and_arch_aliases(self):
        self.file("releases.json", b"committed manifest")
        self.file("linux/arch/x86_64/repo.db.tar.gz", b"signed database")
        (self.root / "linux/arch/x86_64/repo.db").symlink_to("repo.db.tar.gz")
        self.file(".env", b"excluded")
        self.commit()
        self.file("releases.json", b"uncommitted work")
        live = {"releases.json": b"committed manifest", "linux/arch/x86_64/repo.db.tar.gz": b"signed database",
                "linux/arch/x86_64/repo.db": b"signed database"}
        seen = []

        def fetch(url, size):
            path = url.removeprefix("https://repo.vineelsai.dev/")
            seen.append(path)
            self.assertEqual(size, len(live[path]))
            return VERIFY.hashlib.sha256(live[path]).hexdigest()

        result = VERIFY.verify(self.root, fetch=fetch)
        self.assertEqual(3, result["verified_files"])
        self.assertCountEqual(live, seen)

    def test_stale_metadata_and_network_failure_fail_deployment(self):
        self.file("linux/debian/dists/stable/Release", b"current signed metadata")
        self.commit()
        with self.assertRaisesRegex(ValueError, "differs.*Release"):
            VERIFY.verify(self.root, fetch=lambda *_: VERIFY.hashlib.sha256(b"stale").hexdigest())

        def failed(*_):
            raise OSError("unavailable")

        with self.assertRaisesRegex(ValueError, "Cannot verify.*Release"):
            VERIFY.verify(self.root, fetch=failed)

    def test_download_checks_size_and_digest(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://repo.vineelsai.dev/releases.json"

        for body in (b"exact", b"too long", b"x"):
            with self.subTest(body=body), patch.object(VERIFY, "build_opener") as opener:
                opener.return_value.open.return_value = Response(body)
                if body == b"exact":
                    self.assertEqual(VERIFY.hashlib.sha256(body).hexdigest(), VERIFY.live_digest(Response(b"").geturl(), 5))
                else:
                    with self.assertRaisesRegex(ValueError, "size"):
                        VERIFY.live_digest(Response(b"").geturl(), 5)

    def test_url_and_redirects_remain_on_known_https_sites(self):
        request = VERIFY.Request("https://repo.vineelsai.com/releases.json")
        redirects = VERIFY.SiteRedirects()
        accepted = redirects.redirect_request(request, None, 301, "", {}, "https://repo.vineelsai.dev/releases.json")
        self.assertEqual("https://repo.vineelsai.dev/releases.json", accepted.full_url)
        for url in ("http://repo.vineelsai.dev/releases.json", "https://evil.test/releases.json",
                    "https://repo.vineelsai.dev.evil.test/releases.json", "https://user@repo.vineelsai.dev/releases.json",
                    "https://repo.vineelsai.dev:443/releases.json", "https://repo.vineelsai.dev/releases.json?token=x",
                    "https://repo.vineelsai.dev/releases.json#fragment", "http://169.254.169.254/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                redirects.redirect_request(request, None, 302, "", {}, url)


if __name__ == "__main__":
    unittest.main()
