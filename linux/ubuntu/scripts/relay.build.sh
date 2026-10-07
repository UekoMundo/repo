#!/usr/bin/env bash
set -euo pipefail
DISTRO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ROOT=$(cd "$DISTRO/../.." && pwd)
HELPER=${PACKAGE_TOOLS:-"$ROOT/scripts/package-tools.py"}
exec python3 "$HELPER" --package relay --format deb --output "$DISTRO/build/packages" "$@"
