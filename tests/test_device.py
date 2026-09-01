import pytest

import nxds.device as device_module
from nxds.device import (
    BaseTransport,
    nXDS,
    nXDSParseError,
    nXDSProtocolError,
    nXDSResponseMismatchError,
    nXDSTimeoutError,
)
from nxds.registers import (
    FaultRegister,
    SystemStatusRegister1,
    SystemStatusRegister2,
    WarningRegister,
)


class FakeTransport(BaseTransport):
    def __init__(self, responses: list[bytes]) -> None:
        self._responses = responses
        self.writes: list[bytes] = []
        self._is_open = False

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def read_until(self, terminator: bytes, timeout_s: float | None = None) -> bytes:
        if not self._responses:
            return b""
        return self._responses.pop(0)

    def flush_input(self) -> None:
        pass

    def flush_output(self) -> None:
        pass


def _make_device(
    responses: list[bytes],
    *,
    retries: int = 0,
) -> tuple[nXDS, FakeTransport]:
    transport = FakeTransport(responses)
    return nXDS(transport, retries=retries), transport


def test_query_frames_and_parses_response() -> None:
    dev, transport = _make_device([b"S804 75\r"])

    value = dev.query("S804")

    assert value == "75"
    assert transport.writes == [b"?S804\r"]


def test_query_accepts_equals_prefixed_response() -> None:
    dev, transport = _make_device([b"=V810 123.5\r"])

    value = dev.query("V810")

    assert value == "123.5"
    assert transport.writes == [b"?V810\r"]


def test_query_with_explicit_transport() -> None:
    transport = FakeTransport([b"S804 75\r"])
    dev = nXDS(transport)

    value = dev.query("S804")

    assert value == "75"
    assert transport.writes == [b"?S804\r"]


def test_from_serial_port_uses_serial_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport([b"S804 75\r"])

    def _serial_transport_factory(*args, **kwargs):
        return transport

    monkeypatch.setattr(device_module, "SerialTransport", _serial_transport_factory)

    dev = nXDS.from_serial_port("COM9")
    value = dev.query("S804")

    assert value == "75"
    assert transport.writes == [b"?S804\r"]


def test_from_serial_port_forwards_serial_args(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def _serial_transport_factory(*args, **kwargs):
        recorded["args"] = args
        recorded["kwargs"] = kwargs
        return FakeTransport([b"S804 75\r"])

    monkeypatch.setattr(device_module, "SerialTransport", _serial_transport_factory)

    _ = nXDS.from_serial_port(
        "COM7",
        baudrate=9600,
        timeout_s=0.5,
        bytesize=8,
        parity="N",
        stopbits=1,
        retries=2,
        response_timeout_s=0.75,
    )

    assert recorded["args"] == ("COM7",)
    assert recorded["kwargs"] == {
        "baudrate": 9600,
        "timeout_s": 0.5,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
    }


def test_from_tcp_uses_tcp_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport([b"S804 75\r"])

    def _tcp_transport_factory(*args, **kwargs):
        return transport

    monkeypatch.setattr(device_module, "TcpSocketTransport", _tcp_transport_factory)

    dev = nXDS.from_tcp("127.0.0.1", 1234)
    value = dev.query("S804")

    assert value == "75"
    assert transport.writes == [b"?S804\r"]


def test_from_tcp_forwards_tcp_args(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def _tcp_transport_factory(*args, **kwargs):
        recorded["args"] = args
        recorded["kwargs"] = kwargs
        return FakeTransport([b"S804 75\r"])

    monkeypatch.setattr(device_module, "TcpSocketTransport", _tcp_transport_factory)

    _ = nXDS.from_tcp(
        "192.168.1.55",
        7777,
        timeout_s=0.9,
        retries=3,
        response_timeout_s=1.2,
    )

    assert recorded["args"] == ("192.168.1.55", 7777)
    assert recorded["kwargs"] == {
        "timeout_s": 0.9,
    }


@pytest.mark.parametrize(
    ("responses", "retries", "expected_exception"),
    [
        ([b"WRONG 75\r"], 0, nXDSProtocolError),
        ([b""], 0, nXDSTimeoutError),
        ([b"", b""], 1, nXDSTimeoutError),
    ],
)
def test_query_failure_matrix(
    responses: list[bytes],
    retries: int,
    expected_exception: type[Exception],
) -> None:
    dev, _ = _make_device(responses, retries=retries)

    with pytest.raises(expected_exception):
        dev.query("S804")


def test_exchange_does_not_retry_ambiguous_empty_response() -> None:
    dev, transport = _make_device([b"", b"S804 75\r"], retries=1)

    with pytest.raises(nXDSTimeoutError):
        dev.query("S804")

    assert transport.writes == [b"?S804\r"]


def test_exchange_rejects_incomplete_frame_without_retry() -> None:
    dev, transport = _make_device([b"=V809", b"=V809 240;10;100\r"], retries=1)

    with pytest.raises(nXDSTimeoutError, match="missing terminator"):
        dev.query("V809")

    assert transport.writes == [b"?V809\r"]


def test_delayed_v809_response_is_rejected_by_v808_query() -> None:
    dev, transport = _make_device([b"", b"=V809 240;10;100\r"])

    with pytest.raises(nXDSTimeoutError):
        dev.query("V809")
    with pytest.raises(nXDSResponseMismatchError) as exc_info:
        dev.query("V808")

    assert exc_info.value.expected_prefix == "V808"
    assert exc_info.value.received_prefix == "V809"
    assert transport.writes == [b"?V809\r", b"?V808\r"]


def test_inverse_response_order_is_detected() -> None:
    dev, _ = _make_device([b"=V808 30;40\r"])

    with pytest.raises(nXDSResponseMismatchError, match="V809.*V808"):
        dev.query("V809")


def test_state_changing_write_is_never_retried() -> None:
    dev, transport = _make_device([b""], retries=3)

    with pytest.raises(nXDSTimeoutError):
        dev.write("C802 1")

    assert transport.writes == [b"!C802 1\r"]


@pytest.mark.parametrize(
    "response",
    [
        "100;1;2;3",
        "100;1;2;3;4;5",
        "bad;1;2;3;4",
        "100;ZZ;2;3;4",
    ],
)
def test_pump_control_parse_failures(response: str) -> None:
    dev, _ = _make_device([f"V802 {response}\r".encode()])

    with pytest.raises((nXDSProtocolError, nXDSParseError)):
        _ = dev.pump_control


def test_pump_control_status_snapshot_and_split_properties() -> None:
    dev, _ = _make_device([b"V802 120.0;1;2;4;8\r"])

    status = dev.pump_control_status

    assert status.motor_frequency_hz == 120.0
    assert status.system_status_register_1 == SystemStatusRegister1(0x1)
    assert status.system_status_register_2 == SystemStatusRegister2(0x2)
    assert status.warning_register == WarningRegister(0x4)
    assert status.fault_register == FaultRegister(0x8)


def test_split_status_properties_remain_available() -> None:
    dev, _ = _make_device([b"V802 55.0;10;20;40;80\r"] * 5)

    assert dev.motor_frequency == 55
    assert dev.system_status_register_1 == SystemStatusRegister1(0x10)
    assert dev.system_status_register_2 == SystemStatusRegister2(0x20)
    assert dev.warning_register == WarningRegister(0x40)
    assert dev.fault_register == FaultRegister(0x80)


def test_write_validates_ack_prefix() -> None:
    dev, transport = _make_device([b"C802 1\r"])

    ok = dev.write("C802 1")

    assert ok is True
    assert transport.writes == [b"!C802 1\r"]


def test_write_accepts_star_prefixed_ack() -> None:
    dev, transport = _make_device([b"*C802 1\r"])

    ok = dev.write("C802 1")

    assert ok is True
    assert transport.writes == [b"!C802 1\r"]


def test_context_manager_closes_transport() -> None:
    dev, transport = _make_device([b"S804 75\r"])

    with dev as open_dev:
        assert open_dev.is_open

    assert not transport.is_open


def test_normal_speed_threshold_validation_error() -> None:
    dev, _ = _make_device([b"S804 75\r"])

    with pytest.raises(ValueError):
        dev.normal_speed_threshold = 49


def test_set_standby_speed_validation_error() -> None:
    dev, _ = _make_device([b"S805 80\r"])

    with pytest.raises(ValueError):
        dev.set_standby_speed(65)
