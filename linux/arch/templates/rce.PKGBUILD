# Generated in a temporary workspace by scripts/package-tools.py.
pkgname=rce
pkgver='${VERSION}'
pkgrel=1
pkgdesc='Remote command execution CLI'
arch=('${ARCH}')
url='https://github.com/UekoMundo/repo'
options=('!strip' '!debug')
source=('rce')
sha256sums=('${BINARY_SHA256}')

package() {
    install -Dm755 rce "$$pkgdir/usr/bin/rce"
}
