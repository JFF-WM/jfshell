# Install jfshell so it can be started with `jfshell` instead of `python run.py`.
#
#   make install                            user: ~/.local   root: /usr
#   sudo make install PREFIX=/usr           system-wide, command in /usr/bin
#   make install PREFIX=$$HOME/.local       user install, command in ~/.local/bin
#   make install DESTDIR=pkg PREFIX=/usr    staging directory for package builds
#   make uninstall [PREFIX=...]             removes only what install created
#
# Layout ($(PREFIX) = /usr or ~/.local):
#   $(PREFIX)/bin/jfshell                   launcher
#   $(PREFIX)/lib/jfshell/jfshell/          main.py, run.py, src/, config/, resources/
# User data (~/.config/jfshell, ~/.local/share/jfshell) is never touched.

PYTHON ?= python3

ifeq ($(origin PREFIX),undefined)
PREFIX := $(if $(filter 0,$(shell id -u)),/usr,$(HOME)/.local)
endif
BINDIR ?= $(PREFIX)/bin
LIBDIR ?= $(PREFIX)/lib/jfshell
APPDIR := $(LIBDIR)/jfshell

.DEFAULT_GOAL := help
.PHONY: help install uninstall

help:
	@echo "make install    install to $(PREFIX) (override with PREFIX=...)"
	@echo "make uninstall  remove the files installed to $(PREFIX)"

# The byte-compile step matters for read-only installs: run.py only compiles a
# writable tree, and main.py runs with -O (needs the .opt-1.pyc files).
install:
	rm -rf $(DESTDIR)$(APPDIR)
	install -d $(DESTDIR)$(APPDIR) $(DESTDIR)$(BINDIR)
	install -m 644 __init__.py main.py run.py requirements.txt $(DESTDIR)$(APPDIR)/
	cp -r src config resources $(DESTDIR)$(APPDIR)/
	find $(DESTDIR)$(APPDIR) -name __pycache__ -type d -prune -exec rm -rf {} +
	chmod -R u=rwX,go=rX $(DESTDIR)$(APPDIR)
	$(PYTHON) -m compileall -q -o 0 -o 1 -d $(APPDIR) $(DESTDIR)$(APPDIR)
	sed -e 's|@PYTHON@|$(PYTHON)|g' -e 's|@APPDIR@|$(APPDIR)|g' jfshell.in > $(DESTDIR)$(BINDIR)/jfshell
	chmod 755 $(DESTDIR)$(BINDIR)/jfshell
	@echo "Installed: $(BINDIR)/jfshell"

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/jfshell
	rm -rf $(DESTDIR)$(LIBDIR)
