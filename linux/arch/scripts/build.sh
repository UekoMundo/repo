#!/usr/bin/env bash
set -euo pipefail

DISTRO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ROOT=$(cd "$DISTRO/../.." && pwd)
HELPER=${PACKAGE_TOOLS:-"$ROOT/scripts/package-tools.py"}

# Keep the unrelated AUR yay builder, but only on explicit opt-in. Its output
# stays in staging; this script never copies into the signed repository pool.
if [[ "${1:-}" == "--third-party" ]]; then
    if [[ "$#" != 2 || "${2:-}" != "--build" ]]; then
        echo "yay requires: --third-party --build" >&2
        exit 1
    fi
    mkdir -p "$DISTRO/packages/yay"
    cd "$DISTRO/packages/yay"
    curl --fail --location --silent --show-error --max-time 60 -o PKGBUILD \
        "https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=yay-bin"
    sed -i \
        -e 's|^pkgname=yay-bin$|pkgname=yay|' \
        -e '/^conflicts=/a conflicts+=(yay-git yay-bin)' \
        -e '/^package() {$/a\    rm -f ../${pkgname}_${pkgver}_$CARCH.tar.gz' PKGBUILD
    makepkg --clean --sign
    exit 0
fi

for package in ${PACKAGES:-checkout rce vmn vmp}; do
    python3 "$HELPER" --package "$package" --format arch \
        --output "$DISTRO/packages/staged" "$@"
done
