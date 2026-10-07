# Generated in a temporary workspace by scripts/package-tools.py.
# source is the already verified, safely extracted release binary, not a git checkout.
pkgname=vmn
pkgver='${VERSION}'
pkgrel=1
pkgdesc='Manage Node.js versions'
arch=('${ARCH}')
url='https://github.com/UekoMundo/repo'
options=('!strip' '!debug')
source=('vmn')
sha256sums=('${BINARY_SHA256}')

package() {
    install -Dm755 vmn "$$pkgdir/usr/bin/vmn"
}
