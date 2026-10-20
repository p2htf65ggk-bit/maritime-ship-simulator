# SPDX-License-Identifier: MPL-2.0
LOCAL_BIND_HELP = (
    "<b>Bind address: where this computer listens</b><br>"
    "Use an address belonging to the simulator computer, not the sender or receiver.<br><br>"
    "<b>0.0.0.0</b> - listen on all local network adapters (usual choice).<br>"
    "<b>127.0.0.1</b> - connections from this computer only.<br>"
    "<b>Your own network IP</b> - listen only on that adapter.<br><br>"
    "TCP clients connect to the simulator computer's actual IP and port, never to 0.0.0.0."
)

REMOTE_TCP_HELP = (
    "<b>Remote host: the computer running the TCP server</b><br>"
    "The simulator connects to this address and port.<br>"
    "Use the server's network IP or hostname; use 127.0.0.1 only if it runs on this computer.<br>"
    "Do not use 0.0.0.0 as a remote destination."
)
