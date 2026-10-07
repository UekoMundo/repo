#!/usr/bin/env bash
set -euo pipefail

# Stage verified packages only; repository index signing/publication is separate.
cd /home/build/repo
exec make build PACKAGE_ARGS="${PACKAGE_ARGS:-}"
