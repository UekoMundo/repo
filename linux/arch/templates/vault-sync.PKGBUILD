# Generated in a temporary workspace by scripts/package-tools.py.
pkgname=vault-sync
pkgver='${VERSION}'
pkgrel=1
pkgdesc='Vault synchronization CLI'
arch=('${ARCH}')
url='https://github.com/UekoMundo/repo'
depends=('dbus')
options=('!strip' '!debug')
source=('vault-sync')
sha256sums=('${BINARY_SHA256}')

package() {
    install -Dm755 vault-sync "$$pkgdir/usr/bin/vault-sync"
}
