# Generated in a temporary workspace by scripts/package-tools.py.
pkgname=checkout
pkgver='${VERSION}'
pkgrel=1
pkgdesc='Check local projects out to a working directory'
arch=('${ARCH}')
url='https://github.com/UekoMundo/repo'
options=('!strip' '!debug')
source=('checkout')
sha256sums=('${BINARY_SHA256}')

package() {
    install -Dm755 checkout "$$pkgdir/usr/bin/checkout"
}
