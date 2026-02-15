# nXDS

## Usage

Create a driver by passing a transport directly:

- Serial transport:

	from nxds import SerialTransport, nXDS

	transport = SerialTransport("COM3", baudrate=19200, timeout_s=0.25)
	dev = nXDS(transport)

- TCP socket transport:

	from nxds import TcpSocketTransport, nXDS

	transport = TcpSocketTransport("192.168.1.50", 7777, timeout_s=0.25)
	dev = nXDS(transport)

Convenience constructors are also available:

- `nXDS.from_serial_port(...)`
- `nXDS.from_tcp(host, port, ...)`

Use as a context manager:

	with nXDS.from_serial_port("COM3") as dev:
			freq = dev.motor_frequency

## API

### Main class

- `nXDS(transport, retries=0, response_timeout_s=None)`
	- `transport`: any `BaseTransport` implementation
	- `retries`: extra retry attempts for empty/invalid framed responses
	- `response_timeout_s`: per-exchange timeout override

### Factory constructors

- `nXDS.from_serial_port(port, baudrate=19200, timeout_s=0.25, bytesize=8, parity="N", stopbits=1, retries=0, response_timeout_s=None)`
- `nXDS.from_tcp(host, port, timeout_s=0.25, retries=0, response_timeout_s=None)`

### Built-in transports

- `SerialTransport(port, baudrate=19200, timeout_s=0.25, bytesize=8, parity="N", stopbits=1)`
- `TcpSocketTransport(host, port, timeout_s=0.25)`

### Common operations

- Pump control:
	- `start_pump()`
	- `stop_pump()`
	- `standby()`
	- `normal()`
- Low-level protocol:
	- `query(command)`
	- `write(command)`
- Lifecycle:
	- `close()`
	- `is_open`

### Key telemetry / status properties

- `pump_control`
- `motor_frequency`
- `system_status_register_1`
- `system_status_register_2`
- `warning_register`
- `fault_register`
- `temperature`, `pump_temperature`, `pump_controller_temperature`
- `link_voltage`, `motor_current`, `motor_power`
- `run_hours`, `pump_cycles`

### Exceptions

- `nXDSError` (base)
- `nXDSTimeoutError`
- `nXDSProtocolError`
- `nXDSParseError`
- `nXDSUnsupportedTransportOperationError`

