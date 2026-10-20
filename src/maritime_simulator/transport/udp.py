# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import socket
from threading import RLock

from maritime_simulator.domain.enums import UdpMode

from .config import UdpEndpoint, resolve_udp_mode


class UdpSocketPool:
    """One configured socket per endpoint; no per-sentence option mutation."""

    def __init__(self):
        self._sockets: dict[UdpEndpoint, socket.socket] = {}
        self._lock = RLock()
        self._closed = False

    def send(self, endpoint: UdpEndpoint, payload: bytes) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("UDP socket pool is closed")
            handle = self._sockets.get(endpoint)
            if handle is None:
                handle = self._create_socket(endpoint)
                self._sockets[endpoint] = handle
            return handle.sendto(payload, (endpoint.host, endpoint.port))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = tuple(self._sockets.values())
            self._sockets.clear()
        for handle in sockets:
            try:
                handle.close()
            except OSError:
                pass

    @staticmethod
    def _create_socket(endpoint: UdpEndpoint) -> socket.socket:
        handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        mode = resolve_udp_mode(endpoint)
        if mode is UdpMode.BROADCAST:
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        elif mode is UdpMode.MULTICAST:
            handle.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, endpoint.multicast_ttl)
            if endpoint.interface_ip:
                handle.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(endpoint.interface_ip),
                )
        return handle
