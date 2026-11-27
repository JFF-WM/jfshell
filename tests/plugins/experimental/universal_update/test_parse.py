"""Tests for the universal_update listing parsers (pure logic, no subprocess)."""

import importlib.util
import sys
from pathlib import Path

_PARSE = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "plugins"
    / "experimental"
    / "universal_update"
    / "_parse.py"
)
_spec = importlib.util.spec_from_file_location("universal_update_parse", _PARSE)
parse = importlib.util.module_from_spec(_spec)
sys.modules["universal_update_parse"] = parse
_spec.loader.exec_module(parse)

Update = parse.Update


def test_pacman_checkupdates():
    text = "linux 6.12.1-1 -> 6.12.2-1\nmesa 1:24.2.7-1 -> 1:24.3.0-1\n"
    assert parse.parse_pacman(text) == [
        Update("linux", "6.12.1-1", "6.12.2-1"),
        Update("mesa", "1:24.2.7-1", "1:24.3.0-1"),
    ]


def test_pacman_qu_ignored_marker_and_junk():
    text = "glibc 2.40-1 -> 2.41-1 [ignored]\n:: Synchronizing\n\n"
    assert parse.parse_pacman(text) == [Update("glibc", "2.40-1", "2.41-1")]


def test_dnf_check_update():
    text = (
        "kernel.x86_64          6.11.4-301.fc41     updates\n"
        "python3-libs.x86_64    3.13.0-1.fc41       updates-testing\n"
        "Obsoleting Packages\n"
        "foo.noarch             1.0-1               fedora\n"
        "    bar.noarch         0.9-1               @System\n"
    )
    result = parse.parse_dnf(text)
    assert result[0] == Update("kernel", "", "6.11.4-301.fc41", "updates")
    assert result[1].name == "python3-libs"
    assert all(u.name != "bar" for u in result)


def test_apt_simulated_upgrade():
    text = (
        "Reading package lists...\n"
        "Inst libc6 [2.36-9] (2.36-9+deb12u4 Debian:12.5/stable [amd64])\n"
        "Inst newpkg (1.0-1 Debian:12.5/stable [amd64])\n"
        "Conf libc6 (2.36-9+deb12u4 Debian:12.5/stable [amd64])\n"
    )
    assert parse.parse_apt(text) == [
        Update("libc6", "2.36-9", "2.36-9+deb12u4", "Debian:12.5/stable"),
        Update("newpkg", "", "1.0-1", "Debian:12.5/stable"),
    ]


def test_zypper_list_updates():
    text = (
        "S | Repository | Name | Current Version | Available Version | Arch\n"
        "--+------------+------+-----------------+-------------------+-------\n"
        "v | Main       | vim  | 9.1.0-1.1       | 9.1.1-1.1         | x86_64\n"
    )
    assert parse.parse_zypper(text) == [Update("vim", "9.1.0-1.1", "9.1.1-1.1", "Main")]


def test_xbps_dry_run():
    text = (
        "firefox-133.0_1 update x86_64 https://repo-default.voidlinux.org/current 80MB\n"
        "libfoo-bar-1.2_3 install x86_64 https://repo 1MB\n"
        "glibc-2.39_1 hold x86_64 https://repo 1MB\n"
    )
    result = parse.parse_xbps(text)
    assert result[0] == Update(
        "firefox", "", "133.0_1", "https://repo-default.voidlinux.org/current"
    )
    assert result[1].name == "libfoo-bar"
    assert len(result) == 2


def test_apk_version():
    text = (
        "Installed:                                Available:\n"
        "busybox-1.36.1-r29                      < 1.36.1-r30\n"
        "musl-utils-1.2.5-r0                     < 1.2.5-r1 {main}\n"
    )
    assert parse.parse_apk(text) == [
        Update("busybox", "1.36.1-r29", "1.36.1-r30"),
        Update("musl-utils", "1.2.5-r0", "1.2.5-r1", "main"),
    ]


def test_flatpak_columns():
    text = (
        "Firefox\torg.mozilla.firefox\t133.0\tflathub\n"
        "\torg.freedesktop.Platform.GL.default\t\tflathub\n"
    )
    assert parse.parse_flatpak(text) == [
        Update("Firefox", "", "133.0", "flathub"),
        Update("org.freedesktop.Platform.GL.default", "", "", "flathub"),
    ]


def test_empty_output():
    for fn in (
        parse.parse_pacman,
        parse.parse_dnf,
        parse.parse_apt,
        parse.parse_zypper,
        parse.parse_xbps,
        parse.parse_apk,
        parse.parse_flatpak,
    ):
        assert fn("") == []
        assert fn(None) == []


def test_provider_lists_parsed_updates_and_survives_failures():
    import asyncio
    import subprocess

    from src.plugins.experimental.universal_update._providers import PacmanProvider

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"linux 6.12.1-1 -> 6.12.2-1\n", b""

    class FakeAsyncio:
        wait_for = staticmethod(asyncio.wait_for)

        def __init__(self, fail=False):
            self.fail = fail

        async def create_subprocess_exec(self, *args, **kwargs):
            if self.fail:
                raise FileNotFoundError(args[0])
            return FakeProc()

    provider = PacmanProvider()
    ok = asyncio.run(provider.get_updates(FakeAsyncio(), subprocess, 5))
    assert ok == [Update("linux", "6.12.1-1", "6.12.2-1")]
    assert asyncio.run(provider.get_update_count(FakeAsyncio(), subprocess, 5)) == 1
    assert (
        asyncio.run(provider.get_updates(FakeAsyncio(fail=True), subprocess, 5)) == []
    )
