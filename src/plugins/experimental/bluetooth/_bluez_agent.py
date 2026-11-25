"""A minimal BlueZ pairing agent.

It registers with the ``NoInputNoOutput`` capability, so BlueZ only offers
"Just Works" pairing, which is what headphones, speakers and most phones use.
Anything that needs a PIN or passkey is rejected instead of being accepted
blindly, which keeps the pairing protection intact.
"""

from dbus_fast import DBusError
from dbus_fast.service import ServiceInterface, method

AGENT_PATH = "/org/jfshell/bluetooth/agent"
AGENT_CAPABILITY = "NoInputNoOutput"
_REJECTED = "org.bluez.Error.Rejected"


class PairingAgent(ServiceInterface):
    """Implements ``org.bluez.Agent1`` for outgoing, user-initiated pairing."""

    def __init__(self) -> None:
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        """BlueZ is done with the agent."""

    @method()
    def Cancel(self):
        """The pairing was cancelled."""

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: F821
        """Accept; only reached for a pairing the user just started."""

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821
        """Accept profile access for the device being paired."""

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
        """Reject: this agent cannot enter a PIN."""
        raise DBusError(_REJECTED, "A PIN is required")

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
        """Reject: this agent cannot enter a passkey."""
        raise DBusError(_REJECTED, "A passkey is required")

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821
        """Reject: confirming a passkey blindly would defeat its purpose."""
        raise DBusError(_REJECTED, "Passkey confirmation is required")

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821
        """Nothing to display."""

    @method()
    def DisplayPasskey(
        self, device: "o", passkey: "u", entered: "q"  # noqa: F821
    ):
        """Nothing to display."""
