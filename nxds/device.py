from enum import IntEnum
from typing import Tuple

import pyvisa


class SystemStatusRegister1(IntEnum):
    DECELERATION = 0
    ACCELERATION_RUNNING = 1
    STANDBY_SPEED = 2
    NORMAL_SPEED = 3
    ABOVE_RAMP_SPEED = 4
    ABOVE_OVERLOAD_SPEED = 5
    SERIAL_ENABLE = 10


class SystemStatusRegister2(IntEnum):
    UPPER_POWER_REGULATOR_ACTIVE = 0
    LOWER_POWER_REGULATOR_ACTIVE = 1
    UPPER_VOLTAGE_REGULATOR_ACTIVE = 2
    SERVICE_DUE = 4
    WARNING = 6
    ALARM = 7


class WarningRegister(IntEnum):
    LOW_PUMP_CONTROLLER_TEMPERATURE = 1
    PUMP_CONTROLLER_TEMPERATURE_REGULATOR_ACTIVE = (
        6  # current restricted by high pump-controller temperature
    )
    HIHG_PUMP_CONTROLLER_TEMPERATURE = 10
    SELF_TEST_WARNING = 15


class FaultRegister(IntEnum):
    VOLTAGE_TRIP = 1  # excessive link voltage
    CURRENT_TRIP = 2  # excessive link current
    OVER_TEMPERATURE_TRIP = 3  # excessive pump-controller temperature
    UNDER_TEMPERATURE_TRIP = 4  # pump-controller temperature sensor failure
    POWER_STAGE_FAULT = 5
    HW_FAULT_LATCH_SET = 8  # H/W fault latch active, see bits 0-7
    EEPROM_FAULT = 9  # e.g. parameter upload incomplete
    NO_PARAMETER_SET = 11  # parameter set upload required
    SELF_TEST_FAULT = 12
    SERIAL_CONTROL_MODE_INTERLOCK = 13
    OVERLOAD_TIMEOUT = 14
    ACCELERATION_TIMEOUT = 15  # pump frequency did not reach threshold in allowed time


class ServiceRegister(IntEnum):
    TIP_SEAL_SERVICE_DUE = 0
    BEARING_SERVICE_DUE = 1
    CONTROLLER_SERVICE_DUE = 3
    SERVICE_DUE = 7


class ControlMode(IntEnum):
    NONE = 0
    SERIAL = 1
    PARALLEL = 2
    MANUAL = 3


# @overload
# def convert_status_word_to_set_flags(
#     word: str, register: SystemStatusRegister1
# ) -> SystemStatusRegister1:
#     ...


# @overload
# def convert_status_word_to_set_flags(
#     word: str, register: SystemStatusRegister2
# ) -> SystemStatusRegister2:
#     ...


# @overload
# def convert_status_word_to_set_flags(
#     word: str, register: WarningRegister
# ) -> WarningRegister:
#     ...


# @overload
# def convert_status_word_to_set_flags(
#     word: str, register: FaultRegister
# ) -> FaultRegister:
#     ...


# @overload
# def convert_status_word_to_set_flags(
#     word: str, register: ServiceRegister
# ) -> ServiceRegister:
#     ...


# def convert_status_word_to_set_flags(
#     word: str,
#     register: Union[
#         SystemStatusRegister1,
#         SystemStatusRegister2,
#         WarningRegister,
#         FaultRegister,
#         ServiceRegister,
#     ],
# ) -> Union[
#     Tuple[SystemStatusRegister1, ...],
#     Tuple[SystemStatusRegister2, ...],
#     Tuple[WarningRegister, ...],
#     Tuple[FaultRegister, ...],
#     Tuple[ServiceRegister, ...],
# ]:
#     status = 0
#     for idv, val in enumerate(word):
#         status += int(val, base=16) << 4 * (3 - idv)
#     set_flags = tuple(ssr for ssr in register if status >> ssr & 1)
#     return set_flags


class nXDS:
    def __init__(self, resource_name: str):
        self.rm = pyvisa.ResourceManager()
        self.instrument = self.rm.open_resource(
            resource_name=resource_name,
            baud_rate=9600,
            parity=pyvisa.constants.Parity.none,
            read_termination="\r",
            write_termination="\r",
            data_bits=8,
            stop_bits=1,
        )

    def query(self, query: str) -> str:
        ret = self.instrument.query(f"?{query}")  # type: ignore
        assert ret[: len(query)] == query
        ret = ret.strip(query)
        return ret

    def write(self, command: str) -> bool:
        self.instrument.query(f"!{command}")  # type: ignore
        return True

    @property
    def Identification(self) -> str:
        ret = self.query("S0")
        return ret

    @property
    def PumpType(self) -> str:
        ret = self.query("V801")
        return ret

    def start_pump(self) -> None:
        self.write("C802 1")

    def stop_pump(self) -> None:
        self.write("C802 0")

    def standby_speed(self) -> None:
        self.write("C803 0")

    def normal_speed(self) -> None:
        self.write("C803 1")

    @property
    def MotorFrequency(self) -> int:
        ret = self.query("V802")
        values = ret.strip(";")
        return int(values[0])

    @property
    def SystemStatusRegister1(self) -> Tuple[SystemStatusRegister1, ...]:
        ret = self.query("V802")
        values = ret.strip(";")
        status = 0
        for idv, val in enumerate(values[1]):
            status += int(val, base=16) << 4 * (3 - idv)
        set_flags = tuple(ssr for ssr in SystemStatusRegister1 if status >> ssr & 1)
        return set_flags

    @property
    def ControlMode(self) -> ControlMode:
        ret = self.query("V802")
        values = ret.strip(";")
        status = 0
        for idv, val in enumerate(values[0]):
            status += int(val, base=16) << 4 * (3 - idv)
        ctrl_mode = 0
        ctrl_mode += status >> 6 & 1
        ctrl_mode += (status >> 7 & 1) << 1
        ctrl_mode += (status >> 13 & 1) << 2
        return ControlMode(ctrl_mode)

    @property
    def SystemStatusRegister2(self) -> Tuple[SystemStatusRegister2, ...]:
        ret = self.query("V802")
        values = ret.strip(";")
        status = 0
        for idv, val in enumerate(values[1]):
            status += int(val, base=16) << 4 * (3 - idv)
        set_flags = tuple(ssr for ssr in SystemStatusRegister2 if status >> ssr & 1)
        return set_flags

    @property
    def WarningRegister(self) -> Tuple[WarningRegister, ...]:
        ret = self.query("V802")
        values = ret.strip(";")
        status = 0
        for idv, val in enumerate(values[2]):
            status += int(val, base=16) << 4 * (3 - idv)
        set_flags = tuple(ssr for ssr in WarningRegister if status >> ssr & 1)
        return set_flags

    @property
    def FaultRegister(self) -> Tuple[FaultRegister, ...]:
        ret = self.query("V802")
        values = ret.strip(";")
        status = 0
        for idv, val in enumerate(values[3]):
            status += int(val, base=16) << 4 * (3 - idv)
        set_flags = tuple(ssr for ssr in FaultRegister if status >> ssr & 1)
        return set_flags

    @property
    def NormalSpeedThreshold(self) -> int:
        ret = self.query("S804")
        return int(ret)

    def set_normal_speed_threshold(self, threshold: int) -> None:
        assert threshold >= 50 & threshold <= 100
        self.write(f"S804 {threshold}")

    @property
    def StandbySpeed(self) -> int:
        ret = self.query("S805")
        return int(ret)

    def set_standby_speed(self, standby_speed: int, memory: str = "volatile") -> None:
        assert memory in ["volatile", "non-volatile"]
        assert standby_speed >= 66 & standby_speed <= 100
        if memory == "volatile":
            self.write("C805 {standby_speed}")
        else:
            self.write("S805 {standby_speed}")

    @property
    def AutoRun(self) -> bool:
        ret = self.query("S806")
        return bool(ret)

    def enable_auto_run(self) -> None:
        self.write("S806 1")

    def disable_auto_run(self) -> None:
        self.write("S806 0")

    @property
    def ServiceRegister(self) -> Tuple[ServiceRegister, ...]:
        values = self.query("V86")
        status = 0
        for idv, val in enumerate(values):
            status += int(val, base=16) << 4 * (3 - idv)
        set_flags = tuple(ssr for ssr in ServiceRegister if status >> ssr & 1)
        return set_flags

    @property
    def PumpTemperature(self) -> float:
        ret = self.query("V808")
        values = [float(v) for v in ret.strip(";")]
        return values[0]

    @property
    def PumpControllerTemperature(self) -> float:
        ret = self.query("V808")
        values = [float(v) for v in ret.strip(";")]
        return values[1]

    @property
    def LinkVoltage(self) -> float:
        ret = self.query("V809")
        values = [float(v) * 0.1 for v in ret.strip(";")]
        return values[0]

    @property
    def MotorCurrent(self) -> float:
        ret = self.query("V809")
        values = [float(v) * 0.1 for v in ret.strip(";")]
        return values[1]

    @property
    def MotorPower(self) -> float:
        ret = self.query("V809")
        values = [float(v) * 0.1 for v in ret.strip(";")]
        return values[2]

    @property
    def RunHours(self) -> float:
        ret = self.query("V810")
        return float(ret)

    @property
    def PumpCycles(self) -> int:
        ret = self.query("V811")
        return int(ret)

    @property
    def DriveRuntime(self) -> int:
        ret = self.query("V813")
        return int(ret)
