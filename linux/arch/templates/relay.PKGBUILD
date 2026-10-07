# Generated in a temporary workspace by scripts/package-tools.py.
pkgname=relay
pkgver='${VERSION}'
pkgrel=1
pkgdesc='Relay CLI'
arch=('${ARCH}')
url='https://github.com/UekoMundo/repo'
options=('!strip' '!debug')
source=('relay')
sha256sums=('${BINARY_SHA256}')

package() {
    install -Dm755 relay "$$pkgdir/usr/bin/relay"
}
