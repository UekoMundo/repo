#!/usr/bin/env bash
set -euo pipefail

# This container stages packages only. Signing/deploying an APT index is a
# separate, explicitly authorized operator action; never disable HTTPS checks.
cd /home/build/repo
exec make generate PACKAGE_ARGS="${PACKAGE_ARGS:-}"
