#!/usr/bin/env python3
"""Verify live public artifacts against the committed deployment payload."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SPEC = importlib.util.spec_from_file_location("stage_site", Path(__file__).with_name("stage-site.py"))
SITE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SITE)
HOSTS = frozenset({"repo.vineelsai.dev", "repo.vineelsai.com"})


def trusted_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in HOSTS
            or parts.username is not None or parts.password is not None
            or parts.port is not None or parts.query or parts.fragment):
        raise ValueError("Expected an HTTPS URL on the public package site")
    return url


class SiteRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        trusted_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def live_digest(url, size):
    request = Request(trusted_url(url), headers={"Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    received = 0
    with build_opener(SiteRedirects()).open(request, timeout=60) as response:
        trusted_url(response.geturl())
        while True:
            block = response.read(min(1024 * 1024, size - received + 1))
            if not block:
                break
            received += len(block)
            if received > size:
                raise ValueError("Live artifact exceeds committed size")
            digest.update(block)
    if received != size:
        raise ValueError("Live artifact is shorter than committed size")
    return digest.hexdigest()


def verify(source, base_url="https://repo.vineelsai.dev", fetch=live_digest):
    trusted_url(base_url)
    if urlsplit(base_url).path not in ("", "/"):
        raise ValueError("Site URL must not contain a path")
    site = SITE.plan(source)

    def check(item):
        path = item["path"]
        expected = hashlib.sha256(SITE.git(source, "cat-file", "blob", item["oid"])).hexdigest()
        try:
            actual = fetch(f"{base_url.rstrip('/')}/{quote(path, safe='/')}", item["size"])
        except Exception as error:
            raise ValueError(f"Cannot verify live artifact: {path}") from error
        if actual != expected:
            raise ValueError(f"Live artifact differs from committed bytes: {path}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(check, site["files"]))
    return {"commit": site["commit"], "verified_files": len(site["files"]), "site": base_url}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--site", default="https://repo.vineelsai.dev")
    args = parser.parse_args()
    result = verify(args.source, args.site)
    print(f"Verified {result['verified_files']} live public files from {result['commit']} at {result['site']}")


if __name__ == "__main__":
    main()
