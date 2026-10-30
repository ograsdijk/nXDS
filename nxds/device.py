import socket
import time
import warnings
from dataclasses import dataclass
from typing import Literal, Tuple

import serial

from .registers import (
    ControlMode,
    FaultRegister,
    Service,
    ServiceRegister,
    SystemStatusRegister1,
    SystemStatusRegister2,
    WarningRegister,
)

# Used when neither the call, the device nor the transport sets a timeout.
_DEFAULT_RESPONSE_TIMEOUT_S = 1.0


class nXDSError(Exception):
    """Base exception for nXDS communication and protocol errors."""


class nXDSTimeoutError(nXDSError):
    """Raised when a command does not receive a response in time."""


class nXDSCommunicationError(nXDSError):
    """Raised when the transport connection is closed or unusable."""


class nXDSProtocolError(nXDSError):
    """Raised when a response violates expected protocol framing/content."""


class nXDSResponseMismatchError(nXDSProtocolError):
    """Raised when a response belongs to a different outstanding request."""

    def __init__(self, expected_prefix: str, received_prefix: str) -> None:
        self.expected_prefix = expected_prefix
        self.received_prefix = received_prefix
        super().__init__(
            f"Expected response prefix '{expected_prefix}', "
            f"received '{received_prefix}'"
        )


class nXDSParseError(nXDSError):
    """Raised when a response cannot be parsed into expected values."""


class nXDSUnsupportedTransportOperationError(nXDSError):
    """Raised when a transport does not support a requested operation."""


@dataclass(frozen=True)
class PumpControlStatus:
    motor_frequency_hz: float
    system_status_register_1: SystemStatusRegister1
    system_status_register_2: SystemStatusRegister2
    warning_register: WarningRegister
    fault_register: FaultRegister


class _Command:
    START_PUMP = "C802 1"
    STOP_PUMP = "C802 0"
    STANDBY_ON = "C803 1"
    STANDBY_OFF = "C803 0"
    PUMP_CONTROL = "V802"
    NORMAL_SPEED_THRESHOLD = "S804"
    STANDBY_SPEED = "S805"
    STANDBY_SPEED_VOLATILE_PREFIX = "C805"
    AUTO_RUN = "S806"
    TEMPERATURE = "V808"
    LINK_PARAMETERS = "V809"
    RUN_HOURS = "V810"
    PUMP_CYCLES = "V811"
    DRIVE_RUNTIME = "V813"
    TIPSEAL_SERVICE = "V814"
    TIPSEAL_SERVICE_RESET = "C814 1"
    BEARING_SERVICE = "V815"
    BEARING_SERVICE_RESET = "C815 1"
    CUSTOMER_INTERFACE_SW = "S820"
    MOTOR_CONTROL_BOOT = "S822"
    CUSTOMER_INTERFACE_BOOT = "S823"
    SERVICE_SETTINGS = "S825"
    SERVICE_REGISTER = "V826"
    SERVICE_INFO = "S835"


# =============================================================================
# Transport base
# =============================================================================
class BaseTransport:
    """Minimal transport interface used by nXDS."""

    supports_serial_reconfigure: bool = False

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    def is_open(self) -> bool:
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read_until(self, terminator: bytes, timeout_s: float | None = None) -> bytes:
        raise NotImplementedError

    def flush_input(self) -> None:
        raise NotImplementedError

    def flush_output(self) -> None:
        raise NotImplementedError

    def set_baudrate(self, baudrate: int) -> None:
        raise nXDSUnsupportedTransportOperationError(
            "Transport does not support runtime baudrate changes."
        )

    def set_parity(self, parity: Literal["N", "O", "E"]) -> None:
        raise nXDSUnsupportedTransportOperationError(
            "Transport does not support runtime parity changes."
        )


class SerialTransport(BaseTransport):
    """Local serial transport (pyserial)."""

    supports_serial_reconfigure = True

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 19200,
        timeout_s: float = 0.25,
        bytesize: int = serial.EIGHTBITS,
        parity: str = serial.PARITY_NONE,
        stopbits: int = serial.STOPBITS_ONE,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self._ser: serial.Serial | None = None

    def open(self) -> None:
        if self._ser is not None and self._ser.is_open:
            return
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
        )
        self.flush_input()
        self.flush_output()

    def close(self) -> None:
        if self._ser is not None and self._ser.is_open:
            self._ser.close()

    @property
    def is_open(self) -> bool:
        return bool(self._ser is not None and self._ser.is_open)

    def _require_open(self) -> serial.Serial:
        if self._ser is None or not self._ser.is_open:
            raise nXDSError("SerialTransport is not open.")
        return self._ser

    def write(self, data: bytes) -> None:
        ser = self._require_open()
        ser.write(data)
        ser.flush()

    def read_until(self, terminator: bytes, timeout_s: float | None = None) -> bytes:
        ser = self._require_open()
        old_timeout = ser.timeout
        try:
            if timeout_s is not None:
                ser.timeout = timeout_s
            return ser.read_until(terminator)
        finally:
            ser.timeout = old_timeout

    def flush_input(self) -> None:
        self._require_open().reset_input_buffer()

    def flush_output(self) -> None:
        self._require_open().reset_output_buffer()

    def set_baudrate(self, baudrate: int) -> None:
        ser = self._require_open()
        ser.baudrate = baudrate
        self.baudrate = baudrate

    def set_parity(self, parity: Literal["N", "O", "E"]) -> None:
        ser = self._require_open()
        if parity == "N":
            ser.parity = serial.PARITY_NONE
            self.parity = serial.PARITY_NONE
        elif parity == "O":
            ser.parity = serial.PARITY_ODD
            self.parity = serial.PARITY_ODD
        elif parity == "E":
            ser.parity = serial.PARITY_EVEN
            self.parity = serial.PARITY_EVEN
        else:
            raise ValueError("parity must be one of 'N', 'O', 'E'")


class TcpSocketTransport(BaseTransport):
    """TCP socket transport."""

    _FLUSH_MAX_BYTES = 64 * 1024
    _FLUSH_MAX_DURATION_S = 0.05

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout_s: float = 0.25,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None

    def open(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout_s,
            )
            sock.settimeout(self.timeout_s)
        except OSError as exc:
            raise nXDSCommunicationError(
                f"Failed opening TCP connection {self.host}:{self.port}: {exc}"
            ) from exc
        self._sock = sock

    def _invalidate_socket(self) -> None:
        """Detach and best-effort close an untrustworthy TCP socket."""
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def close(self) -> None:
        self._invalidate_socket()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def _require_open(self) -> socket.socket:
        if self._sock is None:
            raise nXDSError("TcpSocketTransport is not open.")
        return self._sock

    def write(self, data: bytes) -> None:
        try:
            self._require_open().sendall(data)
        except OSError as exc:
            self._invalidate_socket()
            raise nXDSCommunicationError(f"TCP write failed: {exc}") from exc

    def read_until(self, terminator: bytes, timeout_s: float | None = None) -> bytes:
        sock = self._require_open()
        old_timeout = sock.gettimeout()
        effective_timeout = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + effective_timeout
        try:
            buf = bytearray()
            while not buf.endswith(terminator):
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    break
                sock.settimeout(remaining_s)
                try:
                    chunk = sock.recv(1)
                except TimeoutError:
                    break
                if not chunk:
                    self._invalidate_socket()
                    raise nXDSCommunicationError(
                        "TCP connection closed while reading response."
                    )
                buf.extend(chunk)
            return bytes(buf)
        except OSError as exc:
            self._invalidate_socket()
            raise nXDSCommunicationError(f"TCP read failed: {exc}") from exc
        finally:
            if self._sock is sock:
                sock.settimeout(old_timeout)

    def flush_input(self) -> None:
        sock = self._require_open()
        old_timeout = sock.gettimeout()
        deadline = time.monotonic() + self._FLUSH_MAX_DURATION_S
        drained = 0
        try:
            sock.setblocking(False)
            while (
                drained < self._FLUSH_MAX_BYTES
                and time.monotonic() < deadline
            ):
                try:
                    chunk = sock.recv(min(4096, self._FLUSH_MAX_BYTES - drained))
                except (BlockingIOError, TimeoutError):
                    break
                if not chunk:
                    self._invalidate_socket()
                    raise nXDSCommunicationError(
                        "TCP connection closed while flushing input."
                    )
                drained += len(chunk)
        except OSError as exc:
            self._invalidate_socket()
            raise nXDSCommunicationError(
                f"TCP input flush failed: {exc}"
            ) from exc
        finally:
            if self._sock is sock:
                sock.settimeout(old_timeout)

    def flush_output(self) -> None:
        # No generic portable TCP output flush.
        return None


class nXDS:
    """
    High-level driver for communicating with an Edwards nXDS vacuum pump.

    Args:
        transport (BaseTransport): Transport implementation used for communication.
    """

    def __init__(
        self,
        transport: BaseTransport,
        *,
        retries: int = 0,
        response_timeout_s: float | None = None,
    ):
        if retries < 0:
            raise ValueError("retries must be >= 0")
        if retries > 0:
            warnings.warn(
                "nXDS no longer retries exchanges, because a re-sent request can "
                "pair a late reply with the wrong command; 'retries' is ignored. "
                "Reconnect after a communication error instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        self.transport = transport

        self.transport.open()
        self._terminator = b"\r"
        self._response_timeout_s = response_timeout_s

    @classmethod
    def from_serial_port(
        cls,
        port: str,
        *,
        baudrate: int = 19200,
        timeout_s: float = 0.25,
        bytesize: int = serial.EIGHTBITS,
        parity: str = serial.PARITY_NONE,
        stopbits: int = serial.STOPBITS_ONE,
        retries: int = 0,
        response_timeout_s: float | None = None,
    ):
        """Create a device instance backed by `SerialTransport`."""
        transport = SerialTransport(
            port,
            baudrate=baudrate,
            timeout_s=timeout_s,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
        )
        return cls(
            transport,
            retries=retries,
            response_timeout_s=response_timeout_s,
        )

    @classmethod
    def from_tcp(
        cls,
        host: str,
        port: int,
        *,
        timeout_s: float = 0.25,
        retries: int = 0,
        response_timeout_s: float | None = None,
    ):
        """Create a device instance backed by `TcpSocketTransport`."""
        transport = TcpSocketTransport(
            host,
            port,
            timeout_s=timeout_s,
        )
        return cls(
            transport,
            retries=retries,
            response_timeout_s=response_timeout_s,
        )

    def __enter__(self):
        """Context manager entry; returns this device instance."""
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """Context manager exit; always closes transport."""
        self.close()
        return False

    def close(self) -> None:
        """Close the underlying transport."""
        self.transport.close()

    @property
    def is_open(self) -> bool:
        """Return whether the underlying transport is currently open."""
        return self.transport.is_open

    def _split_fields(self, response: str, expected: int, command: str) -> list[str]:
        parts = response.split(";")
        if len(parts) != expected:
            raise nXDSProtocolError(
                f"Invalid response for '{command}': expected {expected} fields, got {len(parts)}; response='{response}'"
            )
        return parts

    def _read_scalar(self, register: str) -> str:
        return self.query(register)

    def _read_fields(self, register: str, expected: int) -> list[str]:
        return self._split_fields(self.query(register), expected, register)

    def _read_int(self, register: str) -> int:
        try:
            return int(self._read_scalar(register))
        except ValueError as exc:
            raise nXDSParseError(f"Invalid int response for '{register}'") from exc

    def _read_float(self, register: str) -> float:
        try:
            return float(self._read_scalar(register))
        except ValueError as exc:
            raise nXDSParseError(f"Invalid float response for '{register}'") from exc

    def _write_register(self, register: str, value: int) -> bool:
        return self.write(f"{register} {value}")

    def _parse_status_frame(self, response: str, command: str) -> PumpControlStatus:
        parts = self._split_fields(response, 5, command)
        try:
            motor_freq = float(parts[0])
            ssr1 = SystemStatusRegister1(int(parts[1], 16))
            ssr2 = SystemStatusRegister2(int(parts[2], 16))
            warn = WarningRegister(int(parts[3], 16))
            fault = FaultRegister(int(parts[4], 16))
        except ValueError as exc:
            raise nXDSParseError(
                f"Invalid numeric field in response for '{command}': '{response}'"
            ) from exc
        return PumpControlStatus(
            motor_frequency_hz=motor_freq,
            system_status_register_1=ssr1,
            system_status_register_2=ssr2,
            warning_register=warn,
            fault_register=fault,
        )

    def _exchange(
        self,
        payload: str,
        *,
        timeout_s: float | None = None,
        expect_prefix: str | None = None,
    ) -> str:
        # Never replay a request: a re-sent request can pair a late reply with
        # the wrong command. Instead, only the reply to this request is
        # accepted; a late reply to an earlier request (e.g. one still
        # buffered in a serial-to-TCP gateway) is discarded and reading
        # continues until the matching reply or the deadline.
        if timeout_s is None:
            timeout_s = self._response_timeout_s
        if timeout_s is None:
            timeout_s = getattr(self.transport, "timeout_s", None)
        if timeout_s is None:
            timeout_s = _DEFAULT_RESPONSE_TIMEOUT_S
        deadline = time.monotonic() + timeout_s

        expected_frame = None
        if payload.startswith("?"):
            expected_frame = "="
        elif payload.startswith("!"):
            expected_frame = "*"

        self.transport.flush_input()
        self.transport.write(payload.encode("ascii") + self._terminator)

        mismatch: nXDSProtocolError | None = None
        while True:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                if mismatch is not None:
                    raise mismatch
                raise nXDSTimeoutError(f"Empty response for '{payload}'")
            response = self.transport.read_until(
                self._terminator, timeout_s=remaining_s
            )
            if not response:
                if mismatch is not None:
                    continue
                raise nXDSTimeoutError(f"Empty response for '{payload}'")
            if not response.endswith(self._terminator):
                raise nXDSTimeoutError(
                    f"Incomplete response for '{payload}': missing terminator; "
                    f"partial={response!r}"
                )
            decoded = response.decode("ascii", errors="replace").strip()

            normalized = decoded
            if decoded[:1] in ("=", "*"):
                frame = decoded[0]
                if expected_frame is not None and frame != expected_frame:
                    mismatch = nXDSProtocolError(
                        f"Response frame '{frame}' != '{expected_frame}'"
                    )
                    continue
                normalized = decoded[1:].lstrip()

            prefix_matches = expect_prefix is None or (
                normalized == expect_prefix
                or normalized.startswith((f"{expect_prefix} ", f"{expect_prefix};"))
            )
            if expect_prefix is not None and not prefix_matches:
                received_prefix = normalized.split(maxsplit=1)[0] if normalized else ""
                mismatch = nXDSResponseMismatchError(expect_prefix, received_prefix)
                continue

            return normalized

    def query(self, query: str) -> str:
        """
        Send a query command to the pump and return the response string.

        Args:
            query (str): Command string without leading '?'.

        Returns:
            str: Raw response from the device, stripped of echoed command.
        """
        ret = self._exchange(f"?{query}", expect_prefix=query)
        return ret[len(query) :].lstrip(" ;")

    def write(self, command: str) -> bool:
        """
        Send a write command to the pump (prefixed with '!').

        Args:
            command (str): The command to send.

        Returns:
            bool: True if the command was accepted without error.
        """
        # Match the acknowledgement on the command id (e.g. "C802") only: the
        # rest of the ack is the device's reply, not an echo of the value.
        self._exchange(f"!{command}", expect_prefix=command.split(maxsplit=1)[0])
        return True

    def start_pump(self) -> None:
        """
        Start the pump.

        Sends the command to start the pump via control register C802.
        """
        self.write(_Command.START_PUMP)

    def stop_pump(self) -> None:
        """
        Stop the pump.

        Sends the command to stop the pump via control register C802.
        """
        self.write(_Command.STOP_PUMP)

    def standby(self) -> None:
        """
        Set pump to standby mode.

        Uses control register C803 to reduce pump speed.
        """
        self.write(_Command.STANDBY_ON)

    def normal(self) -> None:
        """
        Set pump to normal operating mode.

        Uses control register C803 to return to full speed from standby.
        """
        self.write(_Command.STANDBY_OFF)

    @property
    def pump_control(
        self,
    ) -> tuple[
        float,
        SystemStatusRegister1,
        SystemStatusRegister2,
        WarningRegister,
        FaultRegister,
    ]:
        """
        Query the pump for motor frequency and status registers (?V802).

        Returns:
            tuple:
                - float: Motor frequency in Hz
                - SystemStatusRegister1: Enum flags for System Status Register 1
                - SystemStatusRegister2: Enum flags for System Status Register 2
                - WarningRegister: Enum flags for warnings
                - FaultRegister: Enum flags for faults
        """
        status = self._parse_status_frame(
            self._read_scalar(_Command.PUMP_CONTROL), _Command.PUMP_CONTROL
        )
        return (
            status.motor_frequency_hz,
            status.system_status_register_1,
            status.system_status_register_2,
            status.warning_register,
            status.fault_register,
        )

    @property
    def pump_control_status(self) -> PumpControlStatus:
        """Typed snapshot of pump control frame V802."""
        return self._parse_status_frame(
            self._read_scalar(_Command.PUMP_CONTROL), _Command.PUMP_CONTROL
        )

    @property
    def motor_frequency(self) -> int:
        """
        Read the pump motor frequency.

        Returns:
            int: Current frequency in Hz.
        """
        return int(self.pump_control_status.motor_frequency_hz)

    @property
    def system_status_register_1(self) -> SystemStatusRegister1:
        """
        Read the first system status register (bits in hex).

        Returns:
            SystemStatusRegister1: Enum flags representing system status bits.
        """
        return self.pump_control_status.system_status_register_1

    @property
    def system_status_register_2(self) -> SystemStatusRegister2:
        """
        Read the second system status register.

        Returns:
            SystemStatusRegister2: Enum flags representing system status bits.
        """
        return self.pump_control_status.system_status_register_2

    @property
    def warning_register(self) -> WarningRegister:
        """
        Read the warning register.

        Returns:
            WarningRegister: Enum flags representing warnings.
        """
        return self.pump_control_status.warning_register

    @property
    def fault_register(self) -> FaultRegister:
        """
        Read the fault register.

        Returns:
            FaultRegister: Enum flags representing current fault status.
        """
        return self.pump_control_status.fault_register

    @property
    def control_mode(self) -> ControlMode:
        """
        Decode the control mode from the bits in System Status Register 1.

        Returns:
            ControlMode: Enum indicating current control mode (none, serial, parallel, manual).
        """
        status = int(self._read_fields(_Command.PUMP_CONTROL, 5)[1], 16)
        ctrl_mode = 0
        ctrl_mode += (status >> 6) & 1  # bit 6
        ctrl_mode += ((status >> 7) & 1) << 1  # bit 7
        ctrl_mode += ((status >> 13) & 1) << 2
        return ControlMode(ctrl_mode)

    @property
    def normal_speed_threshold(self) -> int:
        """
        Get the normal speed threshold percentage.

        Returns:
            int: Normal speed threshold (50–100).
        """
        return self._read_int(_Command.NORMAL_SPEED_THRESHOLD)

    @normal_speed_threshold.setter
    def normal_speed_threshold(self, threshold: int) -> None:
        """
        Set the normal speed threshold.

        Args:
            threshold (int): Percentage value between 50 and 100.

        Raises:
            ValueError: If the threshold is outside 50–100.
        """
        if not 50 <= threshold <= 100:
            raise ValueError("threshold must be between 50 and 100")
        self._write_register(_Command.NORMAL_SPEED_THRESHOLD, threshold)

    @property
    def standby_speed(self) -> int:
        """
        Get the standby speed percentage.

        Returns:
            int: Standby speed (66–100).
        """
        return self._read_int(_Command.STANDBY_SPEED)

    def set_standby_speed(self, standby_speed: int, memory: str = "volatile") -> None:
        """
        Set the standby speed, optionally storing it in non-volatile memory.

        Args:
            standby_speed (int): Speed value (66–100).
            memory (str): Either "volatile" or "non-volatile".

        Raises:
            ValueError: If memory type is invalid or standby_speed out of bounds.
        """
        if memory not in ["volatile", "non-volatile"]:
            raise ValueError("memory must be 'volatile' or 'non-volatile'")
        if not 66 <= standby_speed <= 100:
            raise ValueError("standby_speed must be between 66 and 100")
        if memory == "volatile":
            self._write_register(_Command.STANDBY_SPEED_VOLATILE_PREFIX, standby_speed)
        else:
            self._write_register(_Command.STANDBY_SPEED, standby_speed)

    @property
    def auto_run(self) -> bool:
        """
        Check whether auto-run on power-up is enabled.

        Returns:
            bool: True if auto-run is enabled, False otherwise.
        """
        return bool(self._read_int(_Command.AUTO_RUN))

    @auto_run.setter
    def auto_run(self, enable: bool) -> None:
        """
        Enable or disable auto-run on power-up.

        Args:
            enable (bool): True to enable, False to disable.
        """
        self._write_register(_Command.AUTO_RUN, int(enable))

    @property
    def temperature(self) -> Tuple[float, float]:
        """
        Get the pump and controller temperatures.

        Returns:
            tuple: (pump_temperature_C, controller_temperature_C)
        """
        values = self._read_fields(_Command.TEMPERATURE, 2)
        values = [float(v) for v in values]
        return (values[0], values[1])

    @property
    def pump_temperature(self) -> float:
        """
        Returns the pump body temperature.

        Returns:
            float: Pump temperature in °C.
        """
        return self.temperature[0]

    @property
    def pump_controller_temperature(self) -> float:
        """
        Returns the controller board temperature.

        Returns:
            float: Controller temperature in °C.
        """
        return self.temperature[1]

    @property
    def link_parameters(self) -> Tuple[float, float, float]:
        """
        Get link voltage, motor current, and power.

        Returns:
            Tuple[float, float, float]: (voltage [V], current [A], power [W])
        """
        values = self._read_fields(_Command.LINK_PARAMETERS, 3)
        values = [float(v) * 0.1 for v in values]
        return (values[0], values[1], values[2])

    @property
    def link_voltage(self) -> float:
        """
        Link voltage reported by the drive.

        Returns:
            float: Voltage in volts.
        """
        return self.link_parameters[0]

    @property
    def motor_current(self) -> float:
        """
        Motor current drawn by the pump.

        Returns:
            float: Current in amps.
        """
        return self.link_parameters[1]

    @property
    def motor_power(self) -> float:
        """
        Motor power usage.

        Returns:
            float: Power in watts.
        """
        return self.link_parameters[2]

    @property
    def run_hours(self) -> float:
        """
        Total number of hours the pump has run.

        Returns:
            float: Run time in hours.
        """
        return self._read_float(_Command.RUN_HOURS)

    @property
    def pump_cycles(self) -> int:
        """
        Total number of pump start/stop cycles.

        Returns:
            int: Number of cycles.
        """
        return self._read_int(_Command.PUMP_CYCLES)

    @property
    def drive_runtime(self) -> Tuple[float, float]:
        """
        Drive runtime information.

        Returns:
            Tuple[float, float]: (total hours run, hours until replacement)
        """
        vals = self._read_fields(_Command.DRIVE_RUNTIME, 2)
        return (float(vals[0]), float(vals[1]))

    @property
    def time_since_tipseal_service(self) -> Tuple[float, float]:
        """
        Time since tipseal service.

        Returns:
            Tuple[float, float]: (hours since service, hours until due)
        """
        vals = self._read_fields(_Command.TIPSEAL_SERVICE, 2)
        return (float(vals[0]), float(vals[1]))

    def reset_tipseal_service_timer(self) -> None:
        """
        Reset the tipseal service timer (`!C814 1`).
        """
        self.write(_Command.TIPSEAL_SERVICE_RESET)

    @property
    def time_since_bearing_service(self) -> Tuple[float, float]:
        """
        Time since bearing service.

        Returns:
            Tuple[float, float]: (hours since service, hours until due)
        """
        vals = self._read_fields(_Command.BEARING_SERVICE, 2)
        return (float(vals[0]), float(vals[1]))

    def reset_bearing_service_timer(self) -> None:
        """
        Reset the bearing service timer (`!C815 1`).
        """
        self.write(_Command.BEARING_SERVICE_RESET)

    def get_fault_history(
        self, fault_history: int
    ) -> tuple[
        float,
        SystemStatusRegister1,
        SystemStatusRegister2,
        WarningRegister,
        FaultRegister,
    ]:
        """
        Read fault history entry (1–4).

        Args:
            fault_history (int): History index from 1 to 4.

        Returns:
            tuple: (pump power on time [h], SystemStatusRegister1, SystemStatusRegister2, WarningRegister, FaultRegister)
        """
        if not 1 <= fault_history <= 4:
            raise ValueError("fault_history must be between 1 and 4")

        command = f"V81{fault_history + 5}"
        return self._parse_status_frame(self.query(command), command)

    @property
    def fault_history_1(
        self,
    ) -> tuple[
        float,
        SystemStatusRegister1,
        SystemStatusRegister2,
        WarningRegister,
        FaultRegister,
    ]:
        """
        Retrieve the most recent fault history entry.

        Returns:
            tuple: (pump power on time [h], SSR1, SSR2, warning, fault)
        """
        return self.get_fault_history(1)

    @property
    def fault_history_2(
        self,
    ) -> tuple[
        float,
        SystemStatusRegister1,
        SystemStatusRegister2,
        WarningRegister,
        FaultRegister,
    ]:
        """
        Retrieve the second fault history entry.

        Returns:
            tuple: (pump power on time [h], SSR1, SSR2, warning, fault)
        """
        return self.get_fault_history(2)

    @property
    def fault_history_3(
        self,
    ) -> tuple[
        float,
        SystemStatusRegister1,
        SystemStatusRegister2,
        WarningRegister,
        FaultRegister,
    ]:
        """
        Retrieve the third fault history entry.

        Returns:
            tuple: (pump power on time [h], SSR1, SSR2, warning, fault)
        """
        return self.get_fault_history(3)

    @property
    def fault_history_4(
        self,
    ) -> tuple[
        float,
        SystemStatusRegister1,
        SystemStatusRegister2,
        WarningRegister,
        FaultRegister,
    ]:
        """
        Retrieve the fourth fault history entry.

        Returns:
            tuple: (pump power on time [h], SSR1, SSR2, warning, fault)
        """
        return self.get_fault_history(4)

    @property
    def customer_interface_software_version(self) -> str:
        """
        Get the software version string for the customer interface.

        Returns:
            str: Version string.
        """
        return self.query(_Command.CUSTOMER_INTERFACE_SW)

    @property
    def motor_control_boot_loader_version(self) -> str:
        """
        Get the motor control bootloader firmware version.

        Returns:
            str: Version string.
        """
        return self.query(_Command.MOTOR_CONTROL_BOOT)

    @property
    def customer_interface_boot_loader_version(self) -> str:
        """
        Get the customer interface bootloader firmware version.

        Returns:
            str: Version string.
        """
        return self.query(_Command.CUSTOMER_INTERFACE_BOOT)

    @property
    def service_settings(self) -> Service:
        """
        Get current service settings configuration.

        Returns:
            Service: Enum value for current service display/FAIL output mode.
        """
        return Service(self._read_int(_Command.SERVICE_SETTINGS))

    @property
    def service_register(self) -> ServiceRegister:
        """
        Read the service register (status flags indicating due maintenance).

        Returns:
            ServiceRegister: Bitmask with service-related flags.
        """
        values = self._read_scalar(_Command.SERVICE_REGISTER)
        status = int(values, 16)
        return ServiceRegister(status)

    @property
    def service(self) -> str:
        """
        Return serial numbers and build info (pump, drive module, control PCB).

        Returns:
            str: string encoding pump identity.
        """
        return self.query(_Command.SERVICE_INFO)
