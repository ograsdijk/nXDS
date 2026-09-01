import socket

import pytest

import nxds.device as device_module
from nxds import TcpSocketTransport, nXDSCommunicationError


class FakeSocket:
    def __init__(self, reads=(), *, timeout=0.25, send_error=None):
        self.reads = list(reads)
        self.timeout = timeout
        self.send_error = send_error
        self.send_calls = 0
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def gettimeout(self):
        return self.timeout

    def setblocking(self, enabled):
        self.timeout = None if enabled else 0.0

    def recv(self, _size):
        item = self.reads.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def sendall(self, _data):
        self.send_calls += 1
        if self.send_error is not None:
            raise self.send_error

    def shutdown(self, _how):
        return

    def close(self):
        self.closed = True


def opened_transport(sock):
    transport = TcpSocketTransport("127.0.0.1", 1234)
    transport._sock = sock
    return transport


@pytest.mark.parametrize("failure", [b"", ConnectionResetError("reset"), OSError("broken")])
def test_fatal_read_invalidates_socket(failure):
    sock = FakeSocket([failure])
    transport = opened_transport(sock)

    with pytest.raises(nXDSCommunicationError):
        transport.read_until(b"\r")

    assert not transport.is_open
    assert sock.closed


def test_fatal_write_invalidates_without_retry():
    sock = FakeSocket(send_error=ConnectionResetError("reset"))
    transport = opened_transport(sock)

    with pytest.raises(nXDSCommunicationError):
        transport.write(b"command")

    assert sock.send_calls == 1
    assert not transport.is_open


def test_write_timeout_invalidates_without_retry():
    sock = FakeSocket(send_error=TimeoutError("timed out"))
    transport = opened_transport(sock)

    with pytest.raises(nXDSCommunicationError):
        transport.write(b"command")

    assert sock.send_calls == 1
    assert not transport.is_open
    assert sock.closed


def test_open_after_failure_creates_fresh_socket(monkeypatch):
    failed = FakeSocket([b""])
    fresh = FakeSocket()
    transport = opened_transport(failed)
    with pytest.raises(nXDSCommunicationError):
        transport.read_until(b"\r")

    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: fresh)
    transport.open()

    assert transport._sock is fresh
    assert transport.is_open


def test_bounded_timeout_keeps_socket_open_and_restores_timeout():
    sock = FakeSocket([b"x", TimeoutError("timed out")], timeout=0.25)
    transport = opened_transport(sock)

    assert transport.read_until(b"\r", timeout_s=0.1) == b"x"
    assert transport.is_open
    assert sock.timeout == 0.25


def test_flush_drains_available_input_and_restores_timeout():
    sock = FakeSocket([b"stale", b"bytes", BlockingIOError()], timeout=0.25)
    transport = opened_transport(sock)

    transport.flush_input()

    assert sock.reads == []
    assert sock.timeout == 0.25
    assert transport.is_open


def test_flush_eof_invalidates_socket():
    sock = FakeSocket([b""])
    transport = opened_transport(sock)

    with pytest.raises(nXDSCommunicationError):
        transport.flush_input()

    assert not transport.is_open


def test_read_deadline_stops_continuous_unterminated_input(monkeypatch):
    sock = FakeSocket([b"x"], timeout=0.25)
    transport = opened_transport(sock)
    times = iter((0.0, 0.01, 0.06))
    monkeypatch.setattr(device_module.time, "monotonic", lambda: next(times))

    assert transport.read_until(b"\r", timeout_s=0.05) == b"x"
    assert transport.is_open
    assert sock.timeout == 0.25


def test_flush_stops_at_byte_limit_under_continuous_input(monkeypatch):
    class ContinuousSocket(FakeSocket):
        def __init__(self):
            super().__init__(timeout=0.25)
            self.recv_calls = 0

        def recv(self, size):
            self.recv_calls += 1
            return b"x" * size

    monkeypatch.setattr(device_module.time, "monotonic", lambda: 0.0)
    sock = ContinuousSocket()
    transport = opened_transport(sock)

    transport.flush_input()

    assert sock.recv_calls == TcpSocketTransport._FLUSH_MAX_BYTES // 4096
    assert sock.timeout == 0.25
    assert transport.is_open
