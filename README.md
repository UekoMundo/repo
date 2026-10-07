# UekoMundo package repository

This repository retains historical Linux packages and signed repository metadata.
The first-party Linux builders now consume **public release archives mirrored to
`UekoMundo/repo`**, pinned by SHA256 in the root `releases.json`. They do not clone
or compile the obsolete per-product repositories.

A change in `UekoMundo/Kenkon` is **not** automatically available here: the source
release workflow must publish and mirror a real release, and its actual archive
checksum and size must be recorded in the manifest first. A package entry or a
placeholder Homebrew formula is not evidence of an installable release. In
particular, `relay` and `vault-sync` builders exist but fail clearly when no stable
public Linux archive exists; neither is included in the default build list.

## Plan or stage a first-party package

Requirements: Python 3.9+; `dpkg-deb` for Debian/Ubuntu builds; Arch Linux
`makepkg`, its normal `/etc/makepkg.conf`, and compression tools for Arch builds.
Run `makepkg` as a non-root user. Dry-runs need only Python and the manifest.

From this repository's root:

```sh
# Default is an offline plan: no download, packaging command, or output writes.
python3 scripts/package-tools.py --package vmn --format deb --arch amd64
python3 scripts/package-tools.py --package vmp --format arch --arch x86_64 --dry-run

# Explicit build: download, check archive size + SHA256, validate, then stage.
python3 scripts/package-tools.py --package vmn --format deb --arch arm64 \
  --output /tmp/uekomundo-debs --build
python3 scripts/package-tools.py --package checkout --format arch --arch aarch64 \
  --output /tmp/uekomundo-arch --build
```

Options:

- `--manifest PATH`: defaults to `$RELEASE_MANIFEST`, otherwise this repository's
  root `releases.json`. The manifest uses `schemaVersion: 1` and a `packages` array
  with `name`, `sourceRepository`, `sourcePath`, and historical `releases`. Each
  release records `version`, `sourceTag`, mirrored `tag`, `prerelease`, and `assets`
  with `name`, `sha256`, `size`, and `urls.repo` / `urls.homebrewTap`.
- `--package`: `vmn`, `vmp`, `checkout`, `rce`, `relay`, or `vault-sync`.
- `--format`: `deb` or `arch`.
- `--arch`: `amd64` / `x86_64` or `arm64` / `aarch64`; defaults to the host.
- `--output DIRECTORY`: staging directory; direct helper calls default to
  `build/packages/<format>/<architecture>`. Existing filenames are never replaced.
- `--dry-run`: explicitly request the default behavior. `--build` is required to
  perform downloads and packaging; it is mutually exclusive with `--dry-run`.

Selection uses the highest stable **numeric semantic version with an archive for
that architecture**, not the order of manifest entries, an unversioned `latest`
URL, or a hardcoded package version. If a newer stable release has no archive for
an architecture, the latest compatible stable release is selected. Both
`linux-amd64` / `linux-x86_64` and `linux-arm64` / `linux-aarch64` archive
names are supported, including historical VMP archives. Debian versions drop the leading `v`; Arch
versions also translate a SemVer build-metadata `+` to `_` for `makepkg`.

Only the selected asset's exact HTTPS
`github.com/UekoMundo/repo/releases/download/<tag>/<asset>` URL is accepted;
`homebrewTap` and old upstream URLs are not download fallbacks. TLS remains
verified, and redirects are limited to GitHub and GitHub release-asset hosts.
The helper rejects missing/placeholder digests, invalid sizes, digest or size
mismatches, path traversal, links, special files, and extra archive entries. An
archive must contain one regular binary named for the tool at its root
(`./<tool>` is also allowed). Historical macOS-created archives may additionally
contain one `._<tool>` AppleDouble sidecar; its format and bounds are checked and
it is discarded, never installed. Small historical PAX timestamp/xattr records
are also allowed, with a 64 KiB combined cap enforced before Python's tar parser
runs; path/size/sparse overrides and GNU extensions are rejected. Arbitrary extra
files are still rejected.
The helper installs the binary as `/usr/bin/<tool>` with
mode 0755. No release binary is executed while packaging.

Debian uses `dpkg-deb --build --root-owner-group`; Arch uses the local
`linux/arch/templates/*.PKGBUILD` files with a checksum of the already verified
binary and `makepkg`. Vault Sync declares its native D-Bus runtime dependency
(`libdbus-1-3` on Debian/Ubuntu, `dbus` on Arch). Both stage **unsigned packages**, never regenerate indexes
or deploy. Output into this checkout's signed pools/database directories (or the
current distro bind mount's publication directories) is refused, and an existing
package file or symlink is never overwritten.

## Linux wrappers and Makefiles

```sh
# These default to dry-run and include checkout, rce, vmn, vmp.
make -C linux/debian generate
make -C linux/ubuntu generate
make -C linux/arch build

# Explicit staged builds (run in the appropriate Linux packaging environment).
make -C linux/debian generate PACKAGES='vmn vmp' PACKAGE_ARGS='--arch amd64 --build'
make -C linux/arch build PACKAGES='checkout vmn' PACKAGE_ARGS='--arch aarch64 --build'

# New tools are opt-in and fail if their required public release is absent.
bash linux/ubuntu/scripts/relay.build.sh --arch amd64
bash linux/debian/scripts/vault-sync.build.sh --arch arm64
```

Debian/Ubuntu wrappers default to `linux/<distro>/build/packages`; Arch defaults
to `linux/arch/packages/staged`. Wrapper arguments are forwarded to the helper,
including overrides of `--output` and `--manifest`. `PACKAGE_TOOLS` can override
the shared helper location. Makefiles invoke Bash, not `sh`, for Bash scripts.
Their default `all` target only plans first-party packaging, not scanning/signing.

Unrelated `eza` and `neovim` builders remain available via
`make -C linux/{debian,ubuntu} third-party`. Their existing upstream build and
pool-copy behavior is unchanged; they are **not** covered by this manifest or the
first-party dry-run/verification guarantees. Arch's existing AUR `yay-bin`
handling is available via `make -C linux/arch third-party`; its signed output now
stays in `packages/yay` rather than being copied into the published pool. These
third-party targets perform real builds and require deliberate opt-in.

## Docker integration and publication boundary

The Debian, Ubuntu, and Arch Compose services still work inside their distro
bind mount at `/home/build/repo`. Read-only mounts at `/opt/release-repo` provide
the shared `scripts`, root `releases.json`, and (for Arch) local templates.
`PACKAGE_TOOLS` and `RELEASE_MANIFEST` point at these mounts; the Dockerfiles
provide Python. Staged output stays in the writable distro build directory.
The root manifest must exist before starting a container.

The normal Linux Compose entrypoints now only invoke first-party packaging;
they default to an offline plan and do not import GPG keys, disable HTTPS
verification, scan/sign indexes, or deploy. Linux services no longer mount a
private signing key or require `.env`; existing keys and production workflows
are unchanged. The manifest bind mount fails if the source is missing instead
of creating a directory where the JSON file should be.

```sh
docker compose run --rm debian
# Explicit stage build only; still no signing or deployment.
docker compose run --rm -e PACKAGE_ARGS='--arch amd64 --build' debian
# To choose a subset, invoke the Makefile explicitly:
docker compose run --rm --entrypoint bash debian \
  -lc 'cd /home/build/repo && make generate PACKAGES=vmn PACKAGE_ARGS="--arch amd64"'
```

**Building is not publishing.** Existing signed pools, APT `Packages`/`Release`/
`InRelease`, Arch repository databases, signatures, and index pages are not
updated by the helper. Before clients can install a newly staged version, an
authorized maintainer must inspect it, deliberately add the new version without
replacing historical artifacts, regenerate repository metadata, and sign the
APT release metadata / Arch packages and databases with the proper keys. The
existing explicit `scan` / `release` targets are publication-maintenance tools,
not automatic packaging steps; review them before use. Never upload unsigned
or stale metadata. No production/S3 operation is needed to validate this work.

## Offline tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The stdlib test suite uses deterministic fixture archives and fake network and
packaging calls. It covers stable/architecture selection, legacy VMP naming,
manifest/URL/redirect trust boundaries, SHA256 and size failures, unsafe archive
entries, `/usr/bin` staging, actual version metadata, dry-run observability,
refusal to replace history, protected outputs, wrapper/Makefile wiring, and
mocked `dpkg-deb` / `makepkg` invocations. It performs no public downloads,
repository signing/regeneration, deployment, or production action.
