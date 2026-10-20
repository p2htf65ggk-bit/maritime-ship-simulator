# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Event, Thread

from .config import SerialEndpoint


class SerialSender:
    """Queued serial output with the QSerialPort object owned by its worker."""

    def __init__(self, endpoint: SerialEndpoint):
        self.endpoint = endpoint
        self._queue: Queue[bytes | None] = Queue(maxsize=endpoint.queue_limit)
        self._stop = Event()
        self._last_error = ""
        self._thread = Thread(target=self._run, name=f"serial-output-{endpoint.device}", daemon=True)
        self._thread.start()

    @property
    def last_error(self) -> str:
        return self._last_error

    def send(self, payload: bytes) -> bool:
        if self._stop.is_set():
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except Full:
            self._last_error = "Serial output queue is full"
            return False

    def close(self, timeout_s: float = 2.0) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._thread.join(timeout_s)

    def _run(self) -> None:
        try:
            from PySide6.QtCore import QIODevice
            from PySide6.QtSerialPort import QSerialPort
        except ImportError as error:
            self._last_error = str(error)
            return

        handle = QSerialPort()
        handle.setPortName(self.endpoint.device)
        handle.setBaudRate(self.endpoint.baud)
        handle.setParity({"E": QSerialPort.EvenParity, "O": QSerialPort.OddParity}.get(self.endpoint.parity, QSerialPort.NoParity))
        handle.setStopBits(QSerialPort.TwoStop if self.endpoint.stop_bits == 2 else QSerialPort.OneStop)
        try:
            while not self._stop.is_set():
                try:
                    payload = self._queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    if payload is None:
                        return
                    if not handle.isOpen() and not handle.open(QIODevice.ReadWrite):
                        self._last_error = handle.errorString()
                        continue
                    if handle.write(payload) < 0 or not handle.waitForBytesWritten(1000):
                        self._last_error = handle.errorString()
                        handle.close()
                    else:
                        self._last_error = ""
                finally:
                    self._queue.task_done()
        finally:
            if handle.isOpen():
                handle.close()
