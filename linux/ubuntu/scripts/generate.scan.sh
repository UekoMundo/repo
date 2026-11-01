#!/bin/bash
set -euo pipefail

# Retain every historical package version and index both published architectures.
for arch in amd64 arm64; do
    index="dists/stable/main/binary-${arch}"
    mkdir -p "$index"
    dpkg-scanpackages --multiversion --arch "$arch" pool/ > "$index/Packages"
    # EOF terminates the final Deb822 paragraph; keep one final newline.
    python3 - "$index/Packages" <<'PYTHON'
from pathlib import Path
import sys
path = Path(sys.argv[1])
data = path.read_bytes().rstrip(b"\n")
path.write_bytes(data + b"\n" if data else b"")
PYTHON
    gzip -n -9 < "$index/Packages" > "$index/Packages.gz"
done
