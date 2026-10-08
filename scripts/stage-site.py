#!/usr/bin/env python3
"""Plan/stage only committed public package artifacts; never sync a checkout."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


ROOT_FILES = frozenset({
    "releases.json", "fdroid/index.html", "fdroid/index.png", "fdroid/logo.png",
    "linux/debian/index.html", "linux/debian/gpg", "linux/ubuntu/index.html",
    "linux/ubuntu/gpg", "linux/arch/index.html",
})
COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~:-]*\Z")
APT_INDEX = re.compile(r"(?:InRelease|Release(?:\.gpg)?|Packages|Sources|Contents-[A-Za-z0-9_-]+)(?:\.(?:gz|xz|bz2))?\Z")
ARCH_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~:-]*(?:\.pkg\.tar\.(?:zst|xz|gz)|\.(?:db|files)(?:\.tar\.(?:gz|xz|zst))?)(?:\.sig)?\Z")
ARCH_ALIAS = re.compile(r"([A-Za-z0-9][A-Za-z0-9._+~-]*\.(?:db|files))(\.sig)?\Z")
FDROID_FILES = frozenset({
    "categories.txt", "entry.json", "entry.jar", "index.jar", "index-v1.json",
    "index-v1.jar", "index-v2.json", "index.xml", "index.html", "index.css", "index.png",
})


def public_path(name):
    """Allowlist site paths, not a denylist of today's private/control files."""
    parts = PurePosixPath(name).parts
    if not parts or "/".join(parts) != name or not all(COMPONENT.fullmatch(p) for p in parts):
        return False
    if name in ROOT_FILES:
        return True
    if len(parts) >= 5 and parts[:2] in (("linux", "debian"), ("linux", "ubuntu")):
        if parts[2] == "dists":
            return bool(APT_INDEX.fullmatch(parts[-1]))
        if parts[2] == "pool":
            return parts[-1].endswith(".deb")
    if len(parts) == 4 and parts[:2] == ("linux", "arch") and parts[2] in ("x86_64", "aarch64"):
        return bool(ARCH_FILE.fullmatch(parts[3]))
    if parts[:2] == ("fdroid", "repo"):
        if len(parts) == 3:
            return parts[2] in FDROID_FILES or parts[2].endswith(".apk")
        if len(parts) == 4 and parts[2] == "icons":
            return parts[3].endswith(".png")
    return False


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args])


def plan(source):
    source = Path(source).resolve()
    if Path(git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve() != source:
        raise ValueError("Source must be the Git repository root")
    commit = git(source, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    entries = {}
    for row in git(source, "ls-tree", "-r", "-l", "-z", commit).split(b"\0"):
        if not row:
            continue
        meta, raw_name = row.split(b"\t", 1)
        mode, kind, oid, size = meta.decode().split()
        name = raw_name.decode("utf-8")
        entries[name] = {"mode": mode, "kind": kind, "oid": oid, "size": size}
    selected = []
    for name, entry in sorted(entries.items()):
        if not public_path(name):
            continue
        if entry["kind"] != "blob":
            raise ValueError(f"Public path is not a committed file: {name}")
        if entry["mode"] == "120000":
            # Arch repo-add aliases are needed by clients. Materialize ONLY an
            # exact sibling compressed database (and its matching signature)
            # from committed blobs, never follow a working-tree filesystem link.
            path = PurePosixPath(name)
            alias = ARCH_ALIAS.fullmatch(path.name)
            if path.parts[:2] != ("linux", "arch") or not alias:
                raise ValueError(f"Unapproved public symlink: {name}")
            target = git(source, "cat-file", "blob", entry["oid"]).decode("utf-8")
            stem, signature = alias.groups()
            expected = {f"{stem}.tar.{compression}{signature or ''}" for compression in ("gz", "xz", "zst")}
            target_entry = entries.get(str(path.parent / target))
            if target not in expected or not target_entry or target_entry["mode"] not in ("100644", "100755"):
                raise ValueError(f"Unsafe or missing Arch database alias target: {name}")
            entry = target_entry
        elif entry["mode"] not in ("100644", "100755"):
            raise ValueError(f"Unsupported public file mode: {name}")
        selected.append({"path": name, "oid": entry["oid"], "size": int(entry["size"])})
    if not selected:
        raise ValueError("No committed public artifacts selected")
    return {"commit": commit, "files": selected}


def stage(source, output, site):
    output = Path(output)
    # Never overwrite files or stage in a checkout (even through an ancestor link).
    resolved = output.resolve()
    source = Path(source).resolve()
    if resolved == source or source in resolved.parents:
        raise ValueError("Stage outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    # A failed stage exits nonzero and leaves evidence; CI must never sync it.
    for item in site["files"]:
        destination = output / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            subprocess.run(["git", "-C", str(source), "cat-file", "blob", item["oid"]], stdout=stream, check=True)
        if destination.stat().st_size != item["size"]:
            raise ValueError(f"Committed blob size mismatch: {item['path']}")
        destination.chmod(0o644)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--write", action="store_true", help="Explicitly stage public blobs; otherwise plan only")
    args = parser.parse_args()
    if args.write and args.output is None:
        parser.error("--write requires --output")
    site = plan(args.source)
    if args.write:
        stage(args.source, args.output, site)
    print(json.dumps({"mode": "staged" if args.write else "dry-run", **site}, indent=2))


if __name__ == "__main__":
    main()
