.PHONY: all build third-party release clean predeploy

PACKAGES ?= checkout rce vmn vmp
PACKAGE_ARGS ?=
RELEASE_ARGS ?=

# Default does not sign or regenerate the repository database.
all: build

build:
	PACKAGES="$(PACKAGES)" bash scripts/build.sh $(PACKAGE_ARGS)

third-party:
	bash scripts/build.sh --third-party --build

release:
	bash scripts/release.sh $(RELEASE_ARGS)

clean:
	rm -rf packages

predeploy:
	rm -rf scripts
	rm -rf packages
	rm .gitignore
	rm Makefile
