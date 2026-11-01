#!/bin/bash
set -euo pipefail

# Hash index files relative to the Release directory, never the Release or its
# signatures: their bytes change while signing and cannot hash themselves.
do_hash() {
    local label=$1 command=$2 file relative digest size
    echo "${label}:"
    while IFS= read -r -d '' file; do
        relative=${file#./}
        digest=$("$command" "$file")
        digest=${digest%% *}
        size=$(wc -c < "$file")
        printf '  %s %s %s\n' "$digest" "$size" "$relative"
    done < <(find . -type f ! -path './Release' ! -path './Release.gpg' ! -path './InRelease' -print0 | sort -z)
}

cd dists/stable
cat <<EOF
Origin: Vineel Sai Repository
Label: Vineel Sai
Suite: stable
Codename: stable
Version: 1.0
Architectures: amd64 arm64
Components: main
Description: A software repository by Vineel Sai
Date: $(date -Ru)
EOF
do_hash MD5Sum md5sum
do_hash SHA1 sha1sum
do_hash SHA256 sha256sum
do_hash SHA512 sha512sum
