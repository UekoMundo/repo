#!/usr/bin/env python3
"""Stage Linux packages from checksum-pinned public UekoMundo/repo releases."""

import argparse
from dataclasses import dataclass
import hashlib
import gzip
from http.client import HTTPException
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import string
import subprocess
import sys
import tarfile
import tempfile
from urllib import parse, request

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("vmn", "vmp", "checkout", "rce", "relay", "vault-sync")
ARCHES = {"amd64": ("amd64", "x86_64"), "x86_64": ("amd64", "x86_64"),
          "arm64": ("arm64", "aarch64"), "aarch64": ("arm64", "aarch64")}
STABLE = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z")
MAX_ARCHIVE_SIZE = 256 * 1024 * 1024
MAX_BINARY_SIZE = 512 * 1024 * 1024


class PackageError(Exception):
    pass


@dataclass(frozen=True)
class Selection:
    name: str
    version: str
    tag: str
    asset: str
    url: str
    sha256: str
    size: int


def safe_release_url(tag, asset, url):
    # Match the complete canonical URL, not a hostname substring or URL prefix.
    if (not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+\-]*", tag)
            or any(part in ("", ".", "..") for part in tag.split("/"))):
        raise PackageError("Invalid public release tag")
    expected = "https://github.com/UekoMundo/repo/releases/download/{}/{}".format(
        parse.quote(tag, safe=""), parse.quote(asset, safe=""))
    if url != expected:
        raise PackageError("Asset URL must be the exact public UekoMundo/repo release URL: " + expected)
    return expected


def select_release(manifest, name, linux_arch):
    if (not isinstance(manifest, dict) or type(manifest.get("schemaVersion")) is not int
            or manifest["schemaVersion"] != 1):
        raise PackageError("Manifest must have schemaVersion 1")
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise PackageError("Manifest packages must be a list")
    entries = [p for p in packages if isinstance(p, dict) and p.get("name") == name]
    if len(entries) != 1:
        raise PackageError("Manifest must contain exactly one package entry for " + name)
    releases = entries[0].get("releases")
    if not isinstance(releases, list):
        raise PackageError("Manifest releases must be a list for " + name)
    archive_arches = ("amd64", "x86_64") if linux_arch == "amd64" else ("arm64", "aarch64")
    wanted = [f"{name}-linux-{arch}.tar.gz" for arch in archive_arches]
    candidates = []
    for release in releases:
        if not isinstance(release, dict):
            raise PackageError("Invalid release entry for " + name)
        version = release.get("version", "")
        match = STABLE.fullmatch(version) if isinstance(version, str) else None
        if not match or release.get("prerelease") is not False:
            continue
        assets = release.get("assets", [])
        if not isinstance(assets, list):
            raise PackageError("Release assets must be a list")
        for asset_name in wanted:
            matches = [a for a in assets if isinstance(a, dict) and a.get("name") == asset_name]
            if len(matches) > 1:
                raise PackageError("Duplicate release asset: " + asset_name)
            if matches:
                candidates.append((tuple(int(match[i]) for i in (1, 2, 3)), version,
                                   release, matches[0]))
                break
    if not candidates:
        raise PackageError(f"No stable public Linux {linux_arch} release archive for {name}; "
                           "publish and mirror a real release first (no source-build fallback)")
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, version, release, asset = candidates[0]
    digest, size = asset.get("sha256"), asset.get("size")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
            or digest == "0" * 64):
        raise PackageError("Release asset needs a non-placeholder SHA256 digest")
    if type(size) is not int or not 0 < size <= MAX_ARCHIVE_SIZE:
        raise PackageError("Invalid or excessive release archive size")
    urls = asset.get("urls")
    url = safe_release_url(release.get("tag"), asset["name"],
                           urls.get("repo") if isinstance(urls, dict) else None)
    return Selection(name, version.removeprefix("v"), release["tag"], asset["name"],
                     url, digest.lower(), size)


def validate_redirect(url):
    parts = parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in
            {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
            or parts.username or parts.password or parts.port not in (None, 443)
            or parts.fragment):
        raise PackageError("Untrusted release download redirect: " + url)


class ReleaseRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_redirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(selection, destination):
    opener = request.build_opener(ReleaseRedirectHandler())
    digest = hashlib.sha256()
    count = 0
    with opener.open(selection.url, timeout=60) as response, destination.open("xb") as target:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > selection.size:
                raise PackageError("Downloaded archive exceeds manifest size")
            digest.update(chunk)
            target.write(chunk)
    if count != selection.size:
        raise PackageError("Downloaded archive size does not match manifest")
    if digest.hexdigest() != selection.sha256:
        raise PackageError("SHA256 mismatch for " + selection.asset)


def validate_tar_envelope(archive_path, binary_name):
    # Inspect raw headers BEFORE tarfile can allocate hidden PAX/GNU extension
    # payloads. Only two bounded regular records and bounded zero padding exist.
    seen = set()
    pax_count = 0
    pax_bytes = 0
    with gzip.open(archive_path, "rb") as stream:
        while True:
            header = stream.read(512)
            if len(header) != 512:
                raise PackageError("Truncated release tar archive")
            if header == bytes(512):
                padding = stream.read(65537)
                if len(padding) < 512 or len(padding) > 65536 or any(padding):
                    raise PackageError("Invalid or excessive release tar padding")
                if stream.read(1):
                    raise PackageError("Excessive release tar padding")
                return
            raw_size = header[124:136].strip(b"\x00 ")
            raw_checksum = header[148:156].strip(b"\x00 ")
            if not raw_size or any(c not in b"01234567" for c in raw_size + raw_checksum):
                raise PackageError("Invalid release tar header number")
            size = int(raw_size, 8)
            if not raw_checksum or int(raw_checksum, 8) != sum(header[:148] + b" " * 8 + header[156:]):
                raise PackageError("Invalid release tar header checksum")
            if header[156:157] == b"x":
                # macOS-created historical archives carry timestamps/provenance.
                # Bound extensions BEFORE tarfile reads them; never permit path,
                # size, sparse-file, or other extraction-changing PAX overrides.
                pax_count += 1
                pax_bytes += size
                if pax_count > 2 or not 0 < size <= 65536 or pax_bytes > 65536:
                    raise PackageError("Archive contains extra entries or excessive PAX metadata")
                data = stream.read(((size + 511) // 512) * 512)
                if len(data) != ((size + 511) // 512) * 512:
                    raise PackageError("Truncated PAX metadata")
                payload = data[:size]
                position = 0
                while position < len(payload):
                    separator = payload.find(b" ", position)
                    length = payload[position:separator]
                    if separator < position or not length.isdigit() or len(length) > 8:
                        raise PackageError("Invalid PAX metadata record")
                    end = position + int(length)
                    if end <= separator + 2 or end > len(payload) or payload[end - 1:end] != b"\n":
                        raise PackageError("Invalid PAX metadata bounds")
                    key, equal, _ = payload[separator + 1:end - 1].partition(b"=")
                    if not equal or not (key in (b"mtime", b"atime", b"ctime") or key.startswith((b"LIBARCHIVE.xattr.", b"SCHILY.xattr."))):
                        raise PackageError("Unsafe PAX metadata override")
                    position = end
                continue
            name = header[:100].split(b"\x00", 1)[0]
            sidecars = (f"._{binary_name}".encode(), f"./._{binary_name}".encode())
            binaries = (binary_name.encode(), f"./{binary_name}".encode())
            is_sidecar = name in sidecars
            identity = "metadata" if is_sidecar else "binary"
            if name not in binaries + sidecars or any(header[345:500]):
                raise PackageError("Archive contains extra entries; expected regular binary: " + binary_name)
            if header[156:157] not in (b"0", b"\x00"):
                raise PackageError("Invalid AppleDouble archive metadata" if is_sidecar else "Archive must contain the expected regular binary")
            if identity in seen:
                raise PackageError("Archive contains extra entries; expected only " + binary_name)
            seen.add(identity)
            if is_sidecar:
                if not 26 <= size <= 65536:
                    raise PackageError("Invalid AppleDouble archive metadata")
            elif not 0 < size <= MAX_BINARY_SIZE:
                raise PackageError("Archive must contain the expected regular binary")
            remaining = ((size + 511) // 512) * 512
            while remaining:
                data = stream.read(min(remaining, 65536))
                if not data:
                    raise PackageError("Truncated release tar archive data")
                remaining -= len(data)


def extract_binary(archive_path, binary_name, destination):
    try:
        validate_tar_envelope(archive_path, binary_name)
    except (OSError, EOFError) as error:
        raise PackageError("Invalid compressed release archive: " + str(error)) from error
    # Never extractall. Historical archives made on macOS also contain one
    # AppleDouble metadata sidecar; validate and discard it, never install it.
    with tarfile.open(archive_path, "r:gz") as archive:
        member = None
        metadata_seen = False
        for _ in range(3):
            entry = archive.next()
            if entry is None:
                break
            if entry.name in (binary_name, "./" + binary_name):
                if (member is not None or not entry.isfile() or entry.sparse is not None
                        or not 0 < entry.size <= MAX_BINARY_SIZE):
                    raise PackageError("Archive must contain only the expected regular binary: " + binary_name)
                member = entry
            elif entry.name in ("._" + binary_name, "./._" + binary_name):
                if (metadata_seen or not entry.isfile() or entry.sparse is not None
                        or not 26 <= entry.size <= 65536):
                    raise PackageError("Invalid AppleDouble archive metadata")
                metadata_seen = True
                with archive.extractfile(entry) as sidecar:
                    payload = sidecar.read(65537)
                count = int.from_bytes(payload[24:26], "big")
                header_size = 26 + 12 * count
                if (payload[:8] != b"\x00\x05\x16\x07\x00\x02\x00\x00" or not 1 <= count <= 16
                        or header_size > len(payload) or len(payload) != entry.size):
                    raise PackageError("Invalid AppleDouble archive metadata")
                for offset in range(26, header_size, 12):
                    start = int.from_bytes(payload[offset + 4:offset + 8], "big")
                    length = int.from_bytes(payload[offset + 8:offset + 12], "big")
                    if start < header_size or start + length > len(payload):
                        raise PackageError("Invalid AppleDouble archive metadata bounds")
            else:
                raise PackageError("Archive contains extra entries; expected only " + binary_name)
        if member is None or archive.next() is not None:
            raise PackageError("Archive must contain only the expected regular binary: " + binary_name)
        data = archive.extractfile(member)
        if data is None:
            raise PackageError("Archive binary could not be read")
        with data, destination.open("xb") as target:
            shutil.copyfileobj(data, target)
    destination.chmod(0o755)


def package_version(selection, fmt):
    return selection.version.replace("+", "_") if fmt == "arch" else selection.version


def package_filename(selection, fmt, arch):
    version = package_version(selection, fmt)
    if fmt == "deb":
        return f"{selection.name}_{version}_{arch}.deb"
    return f"{selection.name}-{version}-1-{arch}.pkg.tar.zst"


def validate_output(output, filename):
    # Builds are staging operations, never unsigned publication into tracked pools/indexes.
    output = output.resolve()
    bases = [ROOT / "linux" / distro for distro in ("debian", "ubuntu", "arch")]
    # Also protect the distro bind mount used by Docker entrypoints.
    bases.append(Path.cwd())
    for base in bases:
        for directory in ("pool", "dists", "x86_64", "aarch64"):
            protected = (base / directory).resolve()
            if output == protected or protected in output.parents:
                raise PackageError("Refusing unsigned output into repository publication directory: " + str(output))
    destination = output / filename
    if destination.exists() or destination.is_symlink():
        raise PackageError("Refusing to overwrite existing package: " + str(destination))
    return destination


def build_deb(selection, arch, binary, work, result):
    tree = work / "package"
    (tree / "usr/bin").mkdir(parents=True)
    (tree / "DEBIAN").mkdir()
    for directory in (tree, tree / "usr", tree / "usr/bin", tree / "DEBIAN"):
        directory.chmod(0o755)
    shutil.copyfile(binary, tree / "usr/bin" / selection.name)
    (tree / "usr/bin" / selection.name).chmod(0o755)
    control = (f"Package: {selection.name}\nVersion: {selection.version}\n"
               f"Architecture: {arch}\nSection: utils\nPriority: optional\n"
               "Maintainer: UekoMundo <mail@vineelsai.com>\n"
               + ("Depends: libdbus-1-3\n" if selection.name == "vault-sync" else "")
               + f"Description: {selection.name} from a verified public UekoMundo release\n")
    (tree / "DEBIAN/control").write_text(control, encoding="utf-8")
    (tree / "DEBIAN/control").chmod(0o644)
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(tree), str(result)], check=True)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_arch(selection, arch, binary, work, result):
    template = ROOT / "linux/arch/templates" / (selection.name + ".PKGBUILD")
    content = string.Template(template.read_text(encoding="utf-8")).substitute(
        VERSION=package_version(selection, "arch"), ARCH=arch,
        BINARY_SHA256=file_sha256(binary))
    (work / "PKGBUILD").write_text(content, encoding="utf-8")
    # Explicit CARCH ensures metadata/filename match the selected binary even when
    # staging a foreign-architecture binary. No compilation or execution of it occurs.
    config = work / "makepkg.conf"
    config.write_text("source /etc/makepkg.conf\n" + f"CARCH={shlex.quote(arch)}\n"
                      "PKGEXT='.pkg.tar.zst'\n" + f"PKGDEST={shlex.quote(str(work))}\n",
                      encoding="utf-8")
    subprocess.run(["makepkg", "--config", str(config), "--cleanbuild", "--noconfirm", "--nosign"],
                   cwd=work, check=True)
    if not result.is_file():
        raise PackageError("makepkg did not produce expected package: " + result.name)


def build_package(selection, fmt, arch, destination):
    tool = "dpkg-deb" if fmt == "deb" else "makepkg"
    if shutil.which(tool) is None:
        raise PackageError(f"{tool} is required for --build; use a Linux packaging environment")
    with tempfile.TemporaryDirectory(prefix="package-tools-") as temporary:
        work = Path(temporary)
        archive = work / "release.tar.gz"
        download_archive(selection, archive)
        binary = work / selection.name
        extract_binary(archive, selection.name, binary)
        result = work / destination.name
        if fmt == "deb":
            build_deb(selection, arch, binary, work, result)
        else:
            build_arch(selection, arch, binary, work, result)
        if not result.is_file():
            raise PackageError("Packager did not produce expected package: " + result.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also prevents concurrent builders from overwriting history.
        with result.open("rb") as source, destination.open("xb") as target:
            try:
                shutil.copyfileobj(source, target)
                os.fchmod(target.fileno(), 0o644)
            except BaseException:
                destination.unlink()
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path(os.environ.get("RELEASE_MANIFEST", ROOT / "releases.json")))
    parser.add_argument("--package", required=True, choices=PACKAGES)
    parser.add_argument("--format", required=True, choices=("deb", "arch"))
    parser.add_argument("--arch", choices=tuple(ARCHES), default=platform.machine())
    parser.add_argument("--output", type=Path, help="staging directory (never an existing signed pool)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print plan without network or writes (default)")
    mode.add_argument("--build", action="store_true", help="download, verify, and build a staged unsigned package")
    args = parser.parse_args(argv)
    try:
        if args.arch not in ARCHES:
            raise PackageError("Unsupported architecture: " + args.arch)
        linux_arch, arch_arch = ARCHES[args.arch]
        arch = linux_arch if args.format == "deb" else arch_arch
        with args.manifest.open(encoding="utf-8") as manifest_file:
            selection = select_release(json.load(manifest_file), args.package, linux_arch)
        output = args.output or ROOT / "build/packages" / args.format / arch
        destination = validate_output(output, package_filename(selection, args.format, arch))
        plan = {"mode": "build" if args.build else "dry-run", "package": selection.name,
                "version": selection.version, "packageVersion": package_version(selection, args.format),
                "tag": selection.tag, "format": args.format, "arch": arch,
                "url": selection.url, "sha256": selection.sha256, "size": selection.size,
                "output": str(destination), "installPath": "/usr/bin/" + selection.name,
                "packager": "dpkg-deb --build --root-owner-group" if args.format == "deb"
                else "makepkg --config <temporary-config> --cleanbuild --noconfirm --nosign"}
        print(json.dumps(plan, indent=2))
        if args.build:
            build_package(selection, args.format, arch, destination)
            print("Staged unsigned package: " + str(destination))
        return 0
    except (PackageError, OSError, ValueError, EOFError, HTTPException,
            tarfile.TarError, subprocess.CalledProcessError) as error:
        print("package-tools: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
