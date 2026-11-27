import abc
import shutil
import os

from ._parse import (
    Update,
    parse_apk,
    parse_apt,
    parse_dnf,
    parse_flatpak,
    parse_pacman,
    parse_xbps,
    parse_zypper,
)


async def _run(asyncio_lib, subprocess_lib, timeout, *args) -> tuple[int | None, str]:
    """Runs a listing command: ``(exit code, stdout)``; ``(None, "")`` on failure."""
    try:
        proc = await asyncio_lib.create_subprocess_exec(
            *args,
            stdout=subprocess_lib.PIPE,
            stderr=subprocess_lib.DEVNULL,
        )
    except Exception:
        return None, ""
    try:
        stdout, _ = await asyncio_lib.wait_for(proc.communicate(), timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return None, ""
    return proc.returncode, stdout.decode(errors="replace") if stdout else ""


class UpdateProvider(abc.ABC):
    def __init__(self, custom_command: str | None = None):
        self._custom_command = custom_command

    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @abc.abstractmethod
    def is_available(self) -> bool:
        pass

    @abc.abstractmethod
    def get_default_command(self) -> str:
        pass

    def get_update_command(self) -> str:
        return (
            self._custom_command if self._custom_command else self.get_default_command()
        )

    @abc.abstractmethod
    async def get_updates(
        self, asyncio_lib, subprocess_lib, timeout: int
    ) -> list[Update]:
        """The pending updates; an empty list when there are none or on failure."""

    async def get_update_count(self, asyncio_lib, subprocess_lib, timeout: int) -> int:
        return len(await self.get_updates(asyncio_lib, subprocess_lib, timeout))


class PacmanProvider(UpdateProvider):
    @property
    def name(self):
        return "Pacman"

    def is_available(self):
        if not shutil.which("pacman"):
            return False
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release", "r") as f:
                    content = f.read().lower()
                    if "id=fedora" in content or "id_like=fedora" in content:
                        return False
        except Exception:
            pass
        return True

    def get_default_command(self):
        return "sudo pacman -Syu"

    async def get_updates(self, asyncio_lib, subprocess_lib, timeout):
        # checkupdates (pacman-contrib) syncs a temporary database; without it
        # pacman -Qu still lists what the last -Sy saw.
        if shutil.which("checkupdates"):
            args = ("checkupdates",)
        else:
            args = ("pacman", "-Qu")
        _, out = await _run(asyncio_lib, subprocess_lib, timeout, *args)
        return parse_pacman(out)


class DnfProvider(UpdateProvider):
    @property
    def name(self):
        return "DNF"

    def is_available(self):
        return bool(shutil.which("dnf"))

    def get_default_command(self):
        return "sudo dnf upgrade"

    async def get_updates(self, asyncio_lib, subprocess_lib, timeout):
        code, out = await _run(
            asyncio_lib, subprocess_lib, timeout, "dnf", "check-update", "-q"
        )
        # 100 means updates are available, 0 none, anything else an error.
        return parse_dnf(out) if code == 100 else []


class AptProvider(UpdateProvider):
    @property
    def name(self):
        return "APT"

    def is_available(self):
        return bool(shutil.which("apt"))

    def get_default_command(self):
        return "sudo apt update && sudo apt upgrade -y"

    async def get_updates(self, asyncio_lib, subprocess_lib, timeout):
        _, out = await _run(
            asyncio_lib, subprocess_lib, timeout, "apt-get", "-s", "upgrade"
        )
        return parse_apt(out)


class ZypperProvider(UpdateProvider):
    @property
    def name(self):
        return "Zypper"

    def is_available(self):
        return bool(shutil.which("zypper"))

    def get_default_command(self):
        return "sudo zypper update"

    async def get_updates(self, asyncio_lib, subprocess_lib, timeout):
        _, out = await _run(asyncio_lib, subprocess_lib, timeout, "zypper", "-q", "lu")
        return parse_zypper(out)


class XbpsProvider(UpdateProvider):
    @property
    def name(self):
        return "XBPS"

    def is_available(self):
        return bool(shutil.which("xbps-install"))

    def get_default_command(self):
        return "sudo xbps-install -Su"

    async def get_updates(self, asyncio_lib, subprocess_lib, timeout):
        _, out = await _run(asyncio_lib, subprocess_lib, timeout, "xbps-install", "-un")
        return parse_xbps(out)


class ApkProvider(UpdateProvider):
    @property
    def name(self):
        return "APK"

    def is_available(self):
        return bool(shutil.which("apk"))

    def get_default_command(self):
        return "sudo apk update && sudo apk upgrade"

    async def get_updates(self, asyncio_lib, subprocess_lib, timeout):
        _, out = await _run(
            asyncio_lib, subprocess_lib, timeout, "apk", "version", "-l", "<"
        )
        return parse_apk(out)


class FlatpakProvider(UpdateProvider):
    @property
    def name(self):
        return "Flatpak"

    def is_available(self):
        return bool(shutil.which("flatpak"))

    def get_default_command(self):
        return "flatpak update -y"

    async def get_updates(self, asyncio_lib, subprocess_lib, timeout):
        _, out = await _run(
            asyncio_lib,
            subprocess_lib,
            timeout,
            "flatpak",
            "remote-ls",
            "--updates",
            "--columns=name,application,version,origin",
        )
        return parse_flatpak(out)
