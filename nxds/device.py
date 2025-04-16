from typing import Tuple

import pyvisa

from .registers import (
    ControlMode,
    FaultRegister,
    Service,
    ServiceRegister,
    SystemStatusRegister1,
    SystemStatusRegister2,
    WarningRegister,
)


def serial_connection(resource_name: str):
    """
    Establish a serial (RS232 or RS485) connection to the nXDS pump.

    Args:
        resource_name (str): VISA resource name (e.g., 'ASRL1::INSTR').

    Returns:
        pyvisa.resources.Resource: A configured instrument resource.
    """
    rm = pyvisa.ResourceManager()
    instrument = rm.open_resource(
        resource_name=resource_name,
        baud_rate=9600,
        parity=pyvisa.constants.Parity.none,
        read_termination="\r",
        write_termination="\r",
        data_bits=8,
        stop_bits=1,
    )
    return instrument


def tcpip_connection(resource_name: str):
    """
    Establish a TCP/IP connection (e.g., over serial-to-Ethernet bridge) to the nXDS pump.

    Args:
        resource_name (str): TCP/IP hostname or IP address.

    Returns:
        pyvisa.resources.Resource: A configured instrument resource.
    """
    rm = pyvisa.ResourceManager()
    instrument = rm.open_resource(
        resource_name=f"TCPIP::{resource_name}::SOCKET",
        read_terminaton="\r",  # Potentially a typo in user's code
        write_termination="\r",
    )
    return instrument


class nXDS:
    """
    High-level driver for communicating with an Edwards nXDS vacuum pump via serial or TCP/IP.

    Args:
        resource_name (str): VISA resource name or TCP/IP address.
        connection_type (str, optional): Either "serial" or "tcpip" (case-insensitive).
    """

    def __init__(self, resource_name: str, connection_type: str = "serial"):
        if connection_type.casefold() == "serial":
            self.instrument = serial_connection(resource_name)
        elif connection_type.casefold() == "tcpip":
            self.instrument = tcpip_connection(resource_name)
        else:
            raise ValueError(
                f"connection_type should be SERIAL or TCPIP; not {connection_type}"
            )

    def query(self, query: str) -> str:
        """
        Send a query command to the pump and return the response string.

        Args:
            query (str): Command string without leading '?'.

        Returns:
            str: Raw response from the device, stripped of echoed command.
        """
        ret = self.instrument.query(f"?{query}")
        assert ret[: len(query)] == query, (
            f"Response prefix '{ret[: len(query)]}' != '{query}'"
        )
        ret = ret.strip(query)
        return ret

    def write(self, command: str) -> bool:
        """
        Send a write command to the pump (prefixed with '!').

        Args:
            command (str): The command to send.

        Returns:
            bool: True if the command was accepted without error.
        """
        self.instrument.query(f"!{command}")
        return True

    def start_pump(self) -> None:
        """
        Start the pump.

        Sends the command to start the pump via control register C802.
        """
        self.write("C802 1")

    def stop_pump(self) -> None:
        """
        Stop the pump.

        Sends the command to stop the pump via control register C802.
        """
        self.write("C802 0")

    def standby(self) -> None:
        """
        Set pump to standby mode.

        Uses control register C803 to reduce pump speed.
        """
        self.write("C803 1")

    def normal(self) -> None:
        """
        Set pump to normal operating mode.

        Uses control register C803 to return to full speed from standby.
        """
        self.write("C803 0")

    @property
    def motor_frequency(self) -> int:
        """
        Read the pump motor frequency.

        Returns:
            int: Current frequency in Hz.
        """
        ret = self.query("V802")
        values = ret.split(";")
        return int(values[0])

    @property
    def system_status_register_1(self) -> SystemStatusRegister1:
        """
        Read the first system status register (bits in hex).

        Returns:
            SystemStatusRegister1: Enum flags representing system status bits.
        """
        ret = self.query("V802")
        values = ret.split(";")
        status = int(values[1], 16)
        return SystemStatusRegister1(status)

    @property
    def system_status_register_2(self) -> SystemStatusRegister2:
        """
        Read the second system status register.

        Returns:
            SystemStatusRegister2: Enum flags representing system status bits.
        """
        ret = self.query("V802")
        values = ret.split(";")
        status = int(values[2], 16)
        return SystemStatusRegister2(status)

    @property
    def warning_register(self) -> WarningRegister:
        """
        Read the warning register.

        Returns:
            WarningRegister: Enum flags representing warnings.
        """
        ret = self.query("V802")
        values = ret.split(";")
        status = int(values[3], 16)
        return WarningRegister(status)

    @property
    def fault_register(self) -> FaultRegister:
        """
        Read the fault register.

        Returns:
            FaultRegister: Enum flags representing current fault status.
        """
        ret = self.query("V802")
        values = ret.split(";")
        status = int(values[4], 16)
        return FaultRegister(status)

    @property
    def control_mode(self) -> ControlMode:
        """
        Decode the control mode from the bits in System Status Register 1.

        Returns:
            ControlMode: Enum indicating current control mode (none, serial, parallel, manual).
        """
        ret = self.query("V802")
        values = ret.split(";")
        status = int(values[1], 16)
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
        ret = self.query("S804")
        return int(ret)

    @normal_speed_threshold.setter
    def normal_speed_threshold(self, threshold: int) -> None:
        """
        Set the normal speed threshold.

        Args:
            threshold (int): Percentage value between 50 and 100.

        Raises:
            AssertionError: If the threshold is outside 50–100.
        """
        assert 50 <= threshold <= 100, "threshold must be between 50 and 100"
        self.write(f"S804 {threshold}")

    @property
    def standby_speed(self) -> int:
        """
        Get the standby speed percentage.

        Returns:
            int: Standby speed (66–100).
        """
        ret = self.query("S805")
        return int(ret)

    def set_standby_speed(self, standby_speed: int, memory: str = "volatile") -> None:
        """
        Set the standby speed, optionally storing it in non-volatile memory.

        Args:
            standby_speed (int): Speed value (66–100).
            memory (str): Either "volatile" or "non-volatile".

        Raises:
            AssertionError: If memory type is invalid or standby_speed out of bounds.
        """
        assert memory in ["volatile", "non-volatile"], (
            "memory must be 'volatile' or 'non-volatile'"
        )
        assert 66 <= standby_speed <= 100, "standby_speed must be between 66 and 100"
        if memory == "volatile":
            self.write(f"C805 {standby_speed}")
        else:
            self.write(f"S805 {standby_speed}")

    @property
    def auto_run(self) -> bool:
        """
        Check whether auto-run on power-up is enabled.

        Returns:
            bool: True if auto-run is enabled, False otherwise.
        """
        ret = self.query("S806")
        return bool(int(ret))

    @auto_run.setter
    def auto_run(self, enable: bool) -> None:
        """
        Enable or disable auto-run on power-up.

        Args:
            enable (bool): True to enable, False to disable.
        """
        self.write(f"S806 {int(enable)}")

    @property
    def temperature(self) -> Tuple[float, float]:
        """
        Get the pump and controller temperatures.

        Returns:
            tuple: (pump_temperature_C, controller_temperature_C)
        """
        ret = self.query("V808")
        values = [float(v) for v in ret.split(";")]
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
        ret = self.query("V809")
        values = [float(v) * 0.1 for v in ret.split(";")]
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
        ret = self.query("V810")
        return float(ret)

    @property
    def pump_cycles(self) -> int:
        """
        Total number of pump start/stop cycles.

        Returns:
            int: Number of cycles.
        """
        ret = self.query("V811")
        return int(ret)

    @property
    def drive_runtime(self) -> Tuple[float, float]:
        """
        Drive runtime information.

        Returns:
            Tuple[float, float]: (total hours run, hours until replacement)
        """
        ret = self.query("V813")
        vals = ret.split(";")
        return (float(vals[0]), float(vals[1]))

    @property
    def time_since_tipseal_service(self) -> Tuple[float, float]:
        """
        Time since tipseal service.

        Returns:
            Tuple[float, float]: (hours since service, hours until due)
        """
        ret = self.query("V814")
        vals = ret.split(";")
        return (float(vals[0]), float(vals[1]))

    def reset_tipseal_service_timer(self) -> None:
        """
        Reset the tipseal service timer (`!C814 1`).
        """
        self.write("C814 1")

    @property
    def time_since_bearing_service(self) -> Tuple[float, float]:
        """
        Time since bearing service.

        Returns:
            Tuple[float, float]: (hours since service, hours until due)
        """
        ret = self.query("V815")
        vals = ret.split(";")
        return (float(vals[0]), float(vals[1]))

    def reset_bearing_service_timer(self) -> None:
        """
        Reset the bearing service timer (`!C815 1`).
        """
        self.write("C815 1")

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
        ret = self.query(f"V81{fault_history + 5}")
        values = ret.split(";")
        # time pump-controller has run, then 4 hex words
        # convert time to float, convert each hex word to the respective IntFlag
        float_time = float(values[0])
        ssr1 = SystemStatusRegister1(int(values[1], 16))
        ssr2 = SystemStatusRegister2(int(values[2], 16))
        warn = WarningRegister(int(values[3], 16))
        fault = FaultRegister(int(values[4], 16))
        return (float_time, ssr1, ssr2, warn, fault)

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
        return self.query("S820")

    @property
    def motor_control_boot_loader_version(self) -> str:
        """
        Get the motor control bootloader firmware version.

        Returns:
            str: Version string.
        """
        return self.query("S822")

    @property
    def customer_interface_boot_loader_version(self) -> str:
        """
        Get the customer interface bootloader firmware version.

        Returns:
            str: Version string.
        """
        return self.query("S823")

    @property
    def service_settings(self) -> Service:
        """
        Get current service settings configuration.

        Returns:
            Service: Enum value for current service display/FAIL output mode.
        """
        return Service(int(self.query("S825")))

    @property
    def service_register(self) -> ServiceRegister:
        """
        Read the service register (status flags indicating due maintenance).

        Returns:
            ServiceRegister: Bitmask with service-related flags.
        """
        values = self.query("V826")
        status = int(values, 16)
        return ServiceRegister(status)

    @property
    def service(self) -> str:
        """
        Return serial numbers and build info (pump, drive module, control PCB).

        Returns:
            str: string encoding pump identity.
        """
        return self.query("S835")
