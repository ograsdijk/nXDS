from enum import IntEnum, IntFlag


class SystemStatusRegister1(IntFlag):
    DECELERATION = 1 << 0
    ACCELERATION_RUNNING = 1 << 1
    STANDBY_SPEED = 1 << 2
    NORMAL_SPEED = 1 << 3
    ABOVE_RAMP_SPEED = 1 << 4
    ABOVE_OVERLOAD_SPEED = 1 << 5
    SERIAL_ENABLE = 1 << 10


class SystemStatusRegister2(IntFlag):
    UPPER_POWER_REGULATOR_ACTIVE = 1 << 0
    LOWER_POWER_REGULATOR_ACTIVE = 1 << 1
    UPPER_VOLTAGE_REGULATOR_ACTIVE = 1 << 2
    SERVICE_DUE = 1 << 4
    WARNING = 1 << 6
    ALARM = 1 << 7


class WarningRegister(IntFlag):
    LOW_PUMP_CONTROLLER_TEMPERATURE = 1 << 1
    PUMP_CONTROLLER_TEMPERATURE_REGULATOR_ACTIVE = (
        1 << 6  # current restricted by high pump-controller temperature
    )
    HIGH_PUMP_CONTROLLER_TEMPERATURE = 1 << 10
    SELF_TEST_WARNING = 1 << 15


class FaultRegister(IntFlag):
    VOLTAGE_TRIP = 1 << 1  # excessive link voltage
    CURRENT_TRIP = 1 << 2  # excessive link current
    OVER_TEMPERATURE_TRIP = 1 << 3  # excessive pump-controller temperature
    UNDER_TEMPERATURE_TRIP = 1 << 4  # pump-controller temperature sensor failure
    POWER_STAGE_FAULT = 1 << 5
    HW_FAULT_LATCH_SET = 1 << 8  # H/W fault latch active, see bits 0-7
    EEPROM_FAULT = 1 << 9  # e.g. parameter upload incomplete
    NO_PARAMETER_SET = 1 << 11  # parameter set upload required
    SELF_TEST_FAULT = 1 << 12
    SERIAL_CONTROL_MODE_INTERLOCK = 1 << 13
    OVERLOAD_TIMEOUT = 1 << 14
    ACCELERATION_TIMEOUT = (
        1 << 15
    )  # pump frequency did not reach threshold in allowed time


class ServiceRegister(IntFlag):
    TIP_SEAL_SERVICE_DUE = 1 << 0
    BEARING_SERVICE_DUE = 1 << 1
    CONTROLLER_SERVICE_DUE = 1 << 3
    SERVICE_DUE = 1 << 7


class ControlMode(IntEnum):
    NONE = 0
    SERIAL = 1
    PARALLEL = 2
    MANUAL = 3


class Service(IntFlag):
    SERVICE_LED = 0
    SERVICE_LED_LINE = 1
    NONE = 2
    SERVICE_LINE = 3
