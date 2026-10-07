.PHONY: all generate third-party scan release clean predeploy

# Packaging is a dry-run unless PACKAGE_ARGS includes --build. Publication is separate.
PACKAGES ?= checkout rce vmn vmp
PACKAGE_ARGS ?=

all: generate

generate:
	@set -e; for package in $(PACKAGES); do \
		bash scripts/$$package.build.sh $(PACKAGE_ARGS); \
	done

# Unrelated upstream builders retain their existing behavior; explicit opt-in only.
third-party:
	mkdir -p build
	bash scripts/eza.build.sh
	bash scripts/neovim.build.sh

scan:
	bash scripts/generate.scan.sh

release:
	rm -rf dists/stable/Release
	rm -rf dists/stable/InRelease
	bash scripts/generate.release.sh > dists/stable/Release
	cat dists/stable/Release | gpg --default-key "Vineel Sai" -abs > dists/stable/Release.gpg
	cat dists/stable/Release | gpg --default-key "Vineel Sai" -abs --clearsign > dists/stable/InRelease

clean:
	rm -rf build

predeploy:
	rm -rf scripts
	rm .gitignore
	rm Makefile
