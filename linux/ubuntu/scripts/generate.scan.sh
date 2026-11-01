#!/bin/bash
set -euo pipefail

# Retain every historical package version and index both published architectures.
for arch in amd64 arm64; do
    index="dists/stable/main/binary-${arch}"
    mkdir -p "$index"
    dpkg-scanpackages --multiversion --arch "$arch" pool/ > "$index/Packages"
    gzip -n -9 < "$index/Packages" > "$index/Packages.gz"
done
