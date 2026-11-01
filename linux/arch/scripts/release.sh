#!/bin/bash
set -euo pipefail

mode=${1:---dry-run}
if [[ "$mode" != --dry-run && "$mode" != --write ]] || (( $# > 1 )); then
    echo 'Usage: release.sh [--dry-run|--write] (CPU_ARCH=x86_64 or aarch64)' >&2
    exit 2
fi
arch=${CPU_ARCH:-$(uname -m)}
case "$arch" in x86_64|aarch64) ;; *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; esac
cd "$arch"
shopt -s nullglob
packages=(*.pkg.tar.zst *.pkg.tar.xz)
if (( ${#packages[@]} == 0 )); then
    echo "No packages found for $arch" >&2
    exit 1
fi
command=(repo-add --sign --key "${SIGNING_KEY:-9C3B5573B872CBDECE21747B4431E64723B4ADDE}" --prevent-downgrade --include-sigs vineelsai-arch-repo.db.tar.gz "${packages[@]}")
if [[ "$mode" == --dry-run ]]; then
    printf 'Planned signed database update:'
    printf ' %q' "${command[@]}"
    printf '\n'
    exit 0
fi
# repo-add updates the database atomically. Keep existing databases and every
# historical package; --prevent-downgrade chooses the newest available version.
"${command[@]}"
