import asyncio
import subprocess
import functools
from typing import Optional, Dict, Any, List, Tuple
from src.shared.command_runner import CommandRunner
from ._network_parse import (
    ActiveConnection,
    SavedConnection,
    WifiNetwork,
    parse_saved_wifi,
    parse_wifi_list,
    parse_wifi_radio,
    pick_active_device,
    property_value,
    with_ssid,
)

NmcliDeviceDict = Dict[str, str]


class NetworkCLI:
    """
    An asynchronous client for interacting with the NetworkManager CLI (nmcli).
    This class provides non-blocking wrappers around common nmcli commands,
    designed for use within an asyncio event loop.
    Attributes:
        logger (logging.Logger | Any): A logger instance for reporting operations and errors.
    """

    def __init__(self, panel_instance) -> None:
        """
        Initializes the NetworkCLI instance.
        Args:
            logger: The logger instance to use. Must support standard logging methods (e.g., info, error).
        """
        self.logger = panel_instance.logger
        self.cmd = CommandRunner(panel_instance)

    async def _connect_to_network_async(self, ssid: str) -> None:
        """
        Asynchronously connects to a Wi-Fi network using 'nmcli device wifi connect'.
        This method is a non-blocking wrapper around the external nmcli process.
        Args:
            ssid: The SSID (name) of the Wi-Fi network to connect to.
        """
        self.logger.info(f"CLI: Attempting to connect to network: {ssid}")
        try:
            command: List[str] = ["nmcli", "device", "wifi", "connect", ssid]
            code, stdout, stderr = await self.cmd.run_async(command)

            if code == 0:
                self.logger.info(f"CLI: Successfully connected to {ssid}.")
            else:
                self.logger.error(
                    f"CLI: Failed to connect to {ssid}. nmcli error:\n{stderr}"
                )
        except Exception as e:
            self.logger.error(f"CLI: Unexpected error during network connection: {e}")

    async def run_nmcli_device_show_async(self) -> str:
        """
        Asynchronously runs 'nmcli device show' and returns the raw output.
        Returns:
            The standard output of the command as a string, or an error message
            if the command fails to execute or returns a non-zero exit code.
        """
        try:
            command: List[str] = ["nmcli", "device", "show"]
            code, stdout, stderr = await self.cmd.run_async(command)

            if code == 0:
                return stdout
            else:
                return f"Error running nmcli device show:\n{stderr}"
        except Exception as e:
            return f"Exception while running nmcli:\n{str(e)}"

    async def get_connected_wifi_ssid_async(self) -> Optional[str]:
        """
        Gets the SSID of the currently connected Wi-Fi network using 'nmcli device status'.
        The output is parsed to find a device of type 'wifi' that is in the 'connected' state.
        Returns:
            The SSID (connection name) of the connected Wi-Fi network, or None if no
            Wi-Fi network is connected or an error occurs.
        """
        try:
            command: List[str] = [
                "nmcli",
                "-t",
                "-f",
                "DEVICE,TYPE,STATE,CONNECTION",
                "device",
                "status",
            ]
            code, stdout, _ = await self.cmd.run_async(command)

            if code != 0:
                return None

            for line in stdout.strip().split("\n"):
                parts: List[str] = line.strip().split(":")
                if len(parts) >= 4:
                    type_: str = parts[1]
                    state: str = parts[2]
                    connection: str = parts[3]
                    if type_ == "wifi" and state.lower() == "connected":
                        return connection
            return None
        except Exception as e:
            self.logger.error(f"Error: nmcli command failed to get connected SSID: {e}")
            return None

    def get_connected_wifi_ssid_sync(self) -> Optional[str]:
        """
        Synchronous helper to get the SSID of the currently connected Wi-Fi network.
        This uses the blocking `subprocess.run` and should only be called from an
        executor to avoid blocking the event loop.
        Returns:
            The SSID (connection name) of the connected Wi-Fi network, or None.
        """
        try:
            command: List[str] = [
                "nmcli",
                "-t",
                "-f",
                "DEVICE,TYPE,STATE,CONNECTION",
                "device",
                "status",
            ]
            stdout = self.cmd.run_sync(command)

            if not stdout:
                return None

            for line in stdout.strip().split("\n"):
                parts: List[str] = line.strip().split(":")
                if len(parts) >= 4:
                    type_: str = parts[1]
                    state: str = parts[2]
                    connection: str = parts[3]
                    if type_ == "wifi" and state.lower() == "connected":
                        return connection
            return None
        except Exception as e:
            self.logger.error(f"Error in synchronous SSID check: {e}")
            return None

    async def _get_wifi_signal_strength_async(self, ssid: str) -> int:
        """
        Asynchronously gets the signal strength (in percent) of a given SSID using 'nmcli device wifi list'.
        Args:
            ssid: The SSID of the Wi-Fi network to check.
        Returns:
            The signal strength as an integer percentage (0-100), or 0 if the
            network is not found or an error occurs.
        """
        try:
            command: List[str] = [
                "nmcli",
                "-g",
                "SSID,SIGNAL",
                "device",
                "wifi",
                "list",
            ]
            code, stdout, _ = await self.cmd.run_async(command)

            if code != 0:
                return 0

            for line in stdout.strip().split("\n"):
                if ":" in line:
                    parts: List[str] = line.split(":")
                    if len(parts) >= 2:
                        current_ssid: str = ":".join(parts[:-1]).replace("\\", "")
                        signal: str = parts[-1]
                        if current_ssid == ssid:
                            try:
                                return int(signal)
                            except (ValueError, TypeError):
                                self.logger.warning(
                                    f"Could not parse signal strength for SSID '{ssid}': {signal}"
                                )
                                return 0
            return 0
        except Exception as e:
            self.logger.error(f"Error getting signal strength: {e}")
            return 0

    def get_default_interface_sync(self) -> Optional[str]:
        """
        Synchronously determines the default network interface name (e.g., 'eth0', 'wlan0').
        It does this by reading the '/proc/net/route' file to find the interface
        associated with the default route ('00000000').
        Returns:
            The name of the default network interface as a string, or None if not found or an error occurs.
        """
        try:
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                f.readline()
                for line in f.readlines():
                    parts: List[str] = line.strip().split()
                    if len(parts) > 1 and parts[1] == "00000000":
                        return parts[0]
        except Exception as e:
            self.logger.error(f"Error reading default route: {e}")
        return None

    async def get_default_interface_async(self) -> Optional[str]:
        """
        Asynchronously gets the name of the default network interface.
        The blocking synchronous function `get_default_interface_sync` is run
        in the event loop's default thread pool executor to prevent blocking.
        Returns:
            The name of the default network interface as a string, or None.
        """
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_default_interface_sync)

    def check_interface_carrier_sync(self, interface: str) -> bool:
        """
        Synchronously checks if a network interface's physical carrier is up (i.e., cable plugged in or Wi-Fi radio active).
        This is done by reading the 'carrier' file in the sysfs filesystem.
        Args:
            interface: The name of the network interface (e.g., 'eth0').
        Returns:
            True if the carrier is up (file contains '1'), False otherwise or on error.
        """
        try:
            with open(
                f"/sys/class/net/{interface}/carrier", "r", encoding="utf-8"
            ) as f:
                return f.read().strip() == "1"
        except Exception:
            return False

    async def check_interface_carrier_async(self, interface: str) -> bool:
        """
        Asynchronously checks if a network interface is physically connected (carrier is up).
        Args:
            interface: The name of the network interface (e.g., 'eth0').
        Returns:
            True if the carrier is up, False otherwise.
        """
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(self.check_interface_carrier_sync, interface)
        )

    async def is_internet_connected_async(self) -> bool:
        """
        Asynchronously checks for general internet connectivity based on default route and carrier status.
        This is a lightweight check that relies on the operating system's routing table
        and physical link status, not an external ping.
        Returns:
            True if a default interface is found and its carrier is up, False otherwise.
        """
        interface: Optional[str] = await self.get_default_interface_async()
        if interface and await self.check_interface_carrier_async(interface):
            return True
        return False

    async def scan_networks_async(self) -> Tuple[Optional[int], str]:
        """
        Asynchronously triggers an 'nmcli device wifi rescan' followed by a 'nmcli device wifi list'.
        Returns:
            A tuple containing:
            - The return code of the final 'list' command (Optional[int]).
            - The raw standard output of the 'list' command (str).
        """
        self.logger.info("CLI: Starting async nmcli scan...")
        try:
            await self.cmd.run_async(["nmcli", "device", "wifi", "rescan"])

            list_command: List[str] = [
                "nmcli",
                "-g",
                "SSID,SIGNAL,BSSID",
                "device",
                "wifi",
                "list",
            ]
            code, stdout, _ = await self.cmd.run_async(list_command)
            return code, stdout
        except Exception as e:
            self.logger.error(f"CLI: Unexpected error executing nmcli scan: {e}")
            return None, ""

    async def _apply_config_autoconnect_settings_async(
        self, ssids_to_autoconnect: List[str]
    ) -> None:
        """
        Asynchronously enforces an autoconnect whitelist for all stored Wi-Fi connection profiles using nmcli.
        If a Wi-Fi profile's SSID is in `ssids_to_autoconnect`, its `connection.autoconnect` property is set to 'yes';
        otherwise, it is set to 'no'.
        Args:
            ssids_to_autoconnect: A list of SSIDs that should have autoconnect enabled.
        """
        try:
            list_command: List[str] = [
                "nmcli",
                "-t",
                "-f",
                "NAME,TYPE",
                "connection",
                "show",
            ]
            code, stdout, _ = await self.cmd.run_async(list_command)

            if code != 0:
                return

            all_wifi_connections = [
                line.split(":")[0].strip()
                for line in stdout.strip().split("\n")
                if ":802-11-wireless" in line and line.split(":")[0].strip()
            ]

            for conn_name in all_wifi_connections:
                ssid_command: List[str] = [
                    "nmcli",
                    "-g",
                    "802-11-wireless.ssid",
                    "connection",
                    "show",
                    conn_name,
                ]
                _, ssid_out, _ = await self.cmd.run_async(ssid_command)
                retrieved_ssid: str = ssid_out.strip()
                profile_ssid = retrieved_ssid if retrieved_ssid else conn_name

                autoconnect_state: str = (
                    "yes" if profile_ssid in ssids_to_autoconnect else "no"
                )
                modify_command: List[str] = [
                    "nmcli",
                    "connection",
                    "modify",
                    conn_name,
                    "connection.autoconnect",
                    autoconnect_state,
                ]
                await self.cmd.run_async(modify_command)
        except Exception as e:
            self.logger.error(f"CLI: Error applying autoconnect modification: {e}")

    @staticmethod
    def _short_error(stderr: str, fallback: str) -> str:
        """Reduce an nmcli error to one short line fit for a label."""
        if "secrets were required" in stderr.lower():
            return "Wrong password"
        for line in stderr.strip().splitlines():
            line = line.strip()
            if line:
                return line.removeprefix("Error: ")[:120]
        return fallback

    async def get_wifi_radio_enabled_async(self) -> bool:
        """
        Asynchronously reads the Wi-Fi radio state using 'nmcli radio wifi'.
        Returns:
            True when the radio is enabled, False when disabled or on error.
        """
        code, stdout, _ = await self.cmd.run_async(["nmcli", "radio", "wifi"])
        return code == 0 and parse_wifi_radio(stdout)

    async def set_wifi_radio_async(self, enabled: bool) -> bool:
        """
        Asynchronously turns the Wi-Fi radio on or off.
        Args:
            enabled: True to turn the radio on, False to turn it off.
        Returns:
            True when nmcli accepted the change.
        """
        state = "on" if enabled else "off"
        code, _, stderr = await self.cmd.run_async(["nmcli", "radio", "wifi", state])
        if code != 0:
            self.logger.error(f"CLI: Failed to turn Wi-Fi {state}: {stderr}")
        return code == 0

    async def list_wifi_async(
        self, rescan: bool = False
    ) -> Tuple[bool, List[WifiNetwork]]:
        """
        Asynchronously lists access points, merged by SSID.
        Args:
            rescan: Ask the driver for a fresh scan first. A scan takes a few
                seconds, so the cached list is returned when this is False.
        Returns:
            A tuple of (command succeeded, networks sorted by signal).
        """
        try:
            if rescan:
                await self.cmd.run_async(["nmcli", "device", "wifi", "rescan"])
                await asyncio.sleep(2.5)
            code, stdout, stderr = await self.cmd.run_async(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "IN-USE,SSID,SIGNAL,SECURITY,FREQ,BSSID",
                    "device",
                    "wifi",
                    "list",
                ]
            )
            if code != 0:
                self.logger.error(f"CLI: Wi-Fi list failed: {stderr}")
                return False, []
            return True, parse_wifi_list(stdout)
        except Exception as e:
            self.logger.error(f"CLI: Unexpected error listing Wi-Fi: {e}")
            return False, []

    async def list_saved_wifi_async(self) -> List[SavedConnection]:
        """
        Asynchronously lists stored Wi-Fi profiles with their real SSIDs.
        Returns:
            The saved Wi-Fi profiles, or an empty list on error.
        """
        try:
            code, stdout, _ = await self.cmd.run_async(
                ["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"]
            )
            if code != 0:
                return []
            profiles = parse_saved_wifi(stdout)

            async def resolve(profile: SavedConnection) -> SavedConnection:
                _, ssid, _ = await self.cmd.run_async(
                    [
                        "nmcli",
                        "-g",
                        "802-11-wireless.ssid",
                        "connection",
                        "show",
                        "uuid",
                        profile.uuid,
                    ]
                )
                return with_ssid(profile, ssid.strip())

            return list(await asyncio.gather(*(resolve(p) for p in profiles)))
        except Exception as e:
            self.logger.error(f"CLI: Unexpected error listing saved Wi-Fi: {e}")
            return []

    async def connect_wifi_async(
        self,
        ssid: str,
        password: Optional[str] = None,
        saved_uuid: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Asynchronously connects to a Wi-Fi network.
        A stored profile is activated by UUID so no duplicate profile is made;
        anything else goes through 'nmcli device wifi connect'.
        Args:
            ssid: The SSID of the network.
            password: Passphrase for a new secured network.
            saved_uuid: UUID of the stored profile, when there is one.
        Returns:
            A tuple of (connected, short error message or empty string).
        """
        self.logger.info(f"CLI: Connecting to {ssid}")
        if saved_uuid:
            command = ["nmcli", "--wait", "20", "connection", "up", "uuid", saved_uuid]
        else:
            command = ["nmcli", "--wait", "20", "device", "wifi", "connect", ssid]
            if password:
                command += ["password", password]
        code, _, stderr = await self.cmd.run_async(command)
        if code == 0:
            return True, ""
        self.logger.error(f"CLI: Failed to connect to {ssid}: {stderr}")
        if not saved_uuid:
            # nmcli may leave a half-made profile behind after a failed first
            # attempt, remove it so the network is not listed as saved.
            await self.cmd.run_async(["nmcli", "connection", "delete", "id", ssid])
        return False, self._short_error(stderr, "Could not connect")

    async def disconnect_device_async(self, device: str) -> bool:
        """
        Asynchronously disconnects a device with 'nmcli device disconnect'.
        Args:
            device: The interface name, e.g. 'wlan0'.
        Returns:
            True when the device was disconnected.
        """
        code, _, stderr = await self.cmd.run_async(
            ["nmcli", "device", "disconnect", device]
        )
        if code != 0:
            self.logger.error(f"CLI: Failed to disconnect {device}: {stderr}")
        return code == 0

    async def forget_connection_async(self, uuid: str) -> bool:
        """
        Asynchronously deletes a stored connection profile.
        Args:
            uuid: UUID of the profile to delete.
        Returns:
            True when the profile was deleted.
        """
        code, _, stderr = await self.cmd.run_async(
            ["nmcli", "connection", "delete", "uuid", uuid]
        )
        if code != 0:
            self.logger.error(f"CLI: Failed to delete profile {uuid}: {stderr}")
        return code == 0

    async def get_active_connection_async(
        self, wifi_networks: Optional[List[WifiNetwork]] = None
    ) -> Optional[ActiveConnection]:
        """
        Asynchronously describes the connection in use for the status card.
        Args:
            wifi_networks: A recent Wi-Fi list, used to add signal and band
                for a wireless connection.
        Returns:
            The active connection, or None when nothing is connected.
        """
        try:
            code, stdout, _ = await self.cmd.run_async(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "DEVICE,TYPE,STATE,CONNECTION",
                    "device",
                    "status",
                ]
            )
            if code != 0:
                return None
            picked = pick_active_device(
                stdout, await self.get_default_interface_async()
            )
            if picked is None:
                return None
            device, kind, name = picked
            props: Dict[str, str] = {}
            for entry in self.parse_nmcli_output(
                await self.run_nmcli_device_show_async()
            ):
                if entry.get("GENERAL.DEVICE") == device:
                    props = entry
                    break
            signal: Optional[int] = None
            band = ""
            security = ""
            if kind == "wifi":
                for network in wifi_networks or []:
                    if network.in_use or network.ssid == name:
                        signal, band = network.signal, network.band
                        security = network.security if network.secured else ""
                        break
            return ActiveConnection(
                kind=kind,
                device=device,
                name=name,
                ip=property_value(props, "IP4.ADDRESS"),
                gateway=property_value(props, "IP4.GATEWAY"),
                dns=property_value(props, "IP4.DNS"),
                mac=props.get("GENERAL.HWADDR", ""),
                signal=signal,
                band=band,
                security=security,
            )
        except Exception as e:
            self.logger.error(f"CLI: Error reading active connection: {e}")
            return None

    def parse_nmcli_output(self, raw_output: str) -> List[NmcliDeviceDict]:
        """
        Parses raw 'nmcli device show' output into a list of device dictionaries.
        The nmcli output is block-based, with each block representing a device,
        separated by empty lines. Each line is a key:value pair.
        Args:
            raw_output: The raw string output from the 'nmcli device show' command.
        Returns:
            A list of dictionaries, where each dictionary represents a network device
            and its properties (e.g., {'GENERAL.DEVICE': 'wlan0', 'GENERAL.TYPE': 'wifi', ...}).
            The loopback device ('lo') is explicitly excluded for typical use cases.
        Raises:
            None
        """
        devices: List[NmcliDeviceDict] = []
        current_device: NmcliDeviceDict = {}
        for line in raw_output.strip().splitlines():
            line = line.strip()
            if not line:
                if current_device:
                    if current_device.get("GENERAL.DEVICE") != "lo":
                        devices.append(current_device)
                    current_device = {}
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                current_device[key.strip()] = value.strip()
        if current_device:
            if current_device.get("GENERAL.DEVICE") != "lo":
                devices.append(current_device)
        return devices
