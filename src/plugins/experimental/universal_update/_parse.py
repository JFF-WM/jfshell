"""Parsers for the update listings of each package manager.

Pure functions only: no subprocess, no GTK. Each one takes the text a backend
printed and returns the pending updates as ``Update`` tuples, so the plugin
can show what will be installed and the parsing can be tested on its own.
"""

import re
from typing import NamedTuple


class Update(NamedTuple):
    """One package that the next system update would install or upgrade."""

    name: str
    # Installed version; "" when the backend does not report it.
    old: str
    new: str
    # Repository, remote or origin; "" when unknown.
    source: str = ""


_ARROW = re.compile(r"^(\S+)\s+(\S+)\s+->\s+(\S+)")
_APT_INST = re.compile(r"^Inst\s+(\S+)\s+(?:\[([^\]]+)\]\s+)?\((\S+)\s+([^\s)]+)")


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in (text or "").splitlines() if line.strip()]


def parse_pacman(text: str) -> list[Update]:
    """``checkupdates`` / ``pacman -Qu``: ``name old -> new``.

    ``pacman -Qu`` may append `` [ignored]``, which is skipped with the rest.
    """
    updates = []
    for line in _lines(text):
        m = _ARROW.match(line.strip())
        if m:
            updates.append(Update(m.group(1), m.group(2), m.group(3)))
    return updates


def parse_dnf(text: str) -> list[Update]:
    """``dnf check-update -q``: ``name.arch  version  repo``.

    Section headers (``Obsoleting Packages``, ``Last metadata ...``) and the
    indented lines under them are skipped.
    """
    updates = []
    for line in _lines(text):
        if line[:1].isspace():
            continue
        parts = line.split()
        if len(parts) != 3 or "." not in parts[0]:
            continue
        name = parts[0].rsplit(".", 1)[0]
        updates.append(Update(name, "", parts[1], parts[2]))
    return updates


def parse_apt(text: str) -> list[Update]:
    """``apt-get -s upgrade``: ``Inst name [old] (new repo [arch])``."""
    updates = []
    for line in _lines(text):
        m = _APT_INST.match(line)
        if m:
            source = m.group(4).split(",", 1)[0]
            updates.append(Update(m.group(1), m.group(2) or "", m.group(3), source))
    return updates


def parse_zypper(text: str) -> list[Update]:
    """``zypper -q lu``: ``v | repo | name | current | available | arch``."""
    updates = []
    for line in _lines(text):
        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 5 or cols[0] != "v":
            continue
        updates.append(Update(cols[2], cols[3], cols[4], cols[1]))
    return updates


def _split_pkgver(pkgver: str) -> tuple[str, str]:
    """``name-1.2_1`` -> ``("name", "1.2_1")`` (the version follows the last dash)."""
    if "-" not in pkgver:
        return pkgver, ""
    name, version = pkgver.rsplit("-", 1)
    return name, version


def parse_xbps(text: str) -> list[Update]:
    """``xbps-install -un``: ``name-version action arch repo ...``.

    Only ``update`` rows are kept; new dependencies (``install``) are listed
    too, with no old version.
    """
    updates = []
    for line in _lines(text):
        parts = line.split()
        if len(parts) < 2 or parts[1] not in ("update", "install"):
            continue
        name, version = _split_pkgver(parts[0])
        source = parts[3] if len(parts) > 3 else ""
        updates.append(Update(name, "", version, source))
    return updates


def parse_apk(text: str) -> list[Update]:
    """``apk version -l '<'``: ``name-1.2-r0 < 1.3-r0`` (first line is a header)."""
    updates = []
    for line in _lines(text):
        left, sep, right = line.partition("<")
        if not sep:
            continue
        pkg = left.split()
        new = right.split()
        if not pkg or not new:
            continue
        pieces = pkg[0].rsplit("-", 2)
        if len(pieces) == 3:
            name, old = pieces[0], f"{pieces[1]}-{pieces[2]}"
        else:
            name, old = _split_pkgver(pkg[0])
        source = new[1].strip("{}") if len(new) > 1 else ""
        updates.append(Update(name, old, new[0], source))
    return updates


def parse_flatpak(text: str) -> list[Update]:
    """``flatpak remote-ls --updates --columns=name,application,version,origin``.

    Tab separated. The name falls back to the application id; the version
    is often empty for runtimes.
    """
    updates = []
    for line in _lines(text):
        cols = line.split("\t")
        if len(cols) < 2:
            cols = line.split()
        cols += [""] * (4 - len(cols))
        name, app, version, origin = (c.strip() for c in cols[:4])
        if not (name or app):
            continue
        updates.append(Update(name or app, "", version, origin))
    return updates
