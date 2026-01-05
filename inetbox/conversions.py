from decimal import Decimal
from .translations import TRANSLATIONS_STATES


def int_to_int(value, lang) -> int:
    return int(value)


def bool_to_int(value, lang) -> int:
    return int(value)


def int_to_bool(value, lang) -> bool:
    return bool(value)


# convert two-byte representation of temperature to a Decimal
def temp_code_to_decimal(bytestring, lang) -> str:
    if bytestring == 0xAAA or bytestring == 0xAAAA or bytestring == 0x0000:
        return "0"
    return str((Decimal(bytestring) / Decimal(10) - Decimal(273)))


# convert two-byte representation of temperature to a str
def temp_code_to_string(bytestring, lang) -> str:
    return str(temp_code_to_decimal(bytestring, lang))


# inverse function of the above, observe exception for None
def decimal_to_temp_code(decimal, lang) -> int:
    if decimal is None or decimal < Decimal("5"):
        # return 0xAAA
        return 0x00
    return int((decimal + Decimal(273)) * Decimal(10))


def string_to_temp_code(string, lang) -> int:
    return decimal_to_temp_code(Decimal(string), lang)


# convert two-byte representation of temperature to a water target temperature string
def water_temp_code_to_string(bytestring, lang) -> str:
    strings = TRANSLATIONS_STATES[lang]["target_temp_water"]
    decimal = temp_code_to_decimal(bytestring, lang)
    try:
        return strings[decimal]
    except KeyError:
        print(f"Unknown water target temperature code: {bytestring}")
        return TRANSLATIONS_STATES[lang]["unknown"]


def string_to_water_temp_code(string, lang) -> int:
    for code, name in TRANSLATIONS_STATES[lang]["target_temp_water"].items():
        if name == string:
            return decimal_to_temp_code(Decimal(code), lang)
    raise ValueError(f"Invalid water target temperature code: {string}")


# error status is 1 if a warning exists, rest of values unknown yet
def operating_status_to_string(operating_status, lang) -> str:
    strings = TRANSLATIONS_STATES[lang]["operating_status"]

    if operating_status in strings:
        return strings[operating_status]
    else:
        print(f"Unknown operating status code: {operating_status}")
        return TRANSLATIONS_STATES[lang]["unknown"]


# error code is two bytes, first byte * 100 + second byte is the error code
def error_code_to_string(error_code_bytes, lang) -> str:
    error_code = error_code_bytes[1] * 100 + error_code_bytes[0]
    return str(error_code)


# Electric heating power level is stored as a two-byte integer and has
# the values 0, 900, or 1800
def el_power_code_to_string(el_power_code, lang) -> str:
    strings = TRANSLATIONS_STATES[lang]["el_power_level"]

    try:
        return strings[el_power_code]
    except KeyError:
        print(f"Unknown electric heating power code: {el_power_code}")
        return TRANSLATIONS_STATES[lang]["unknown"]


# inverse of the above
def string_to_el_power_code(string, lang) -> int:
    for code, name in TRANSLATIONS_STATES[lang]["el_power_level"].items():
        if name == string:
            return code

    raise ValueError(f"Invalid electric heating power code: {code}")


def energy_mix_code_to_string(energy_mix_code, lang) -> str:
    strings = TRANSLATIONS_STATES[lang]["energy_mix"]
    try:
        return strings[energy_mix_code]
    except KeyError:
        print(f"Unknown energy mix code: {energy_mix_code}")
        return TRANSLATIONS_STATES[lang]["unknown"]


# inverse of the above
def string_to_energy_mix_code(string, lang) -> int:
    for code, name in TRANSLATIONS_STATES[lang]["energy_mix"].items():
        if name == string:
            return code
    raise ValueError(f"Invalid energy mix code: {string}")


def heating_mode_to_string(heating_mode, lang) -> str:
    strings = TRANSLATIONS_STATES[lang]["heating_mode"]
    try:
        return strings[heating_mode]
    except KeyError:
        print(f"Unknown heating mode code: {heating_mode}")
        return TRANSLATIONS_STATES[lang]["unknown"]


# inverse of the above
def string_to_heating_mode(string, lang) -> int:
    for code, name in TRANSLATIONS_STATES[lang]["heating_mode"].items():
        if name == string:
            return code
    raise ValueError(f"Invalid heating mode code: {string}")


def clock_mode_to_string(clock_mode, lang) -> str:
    strings = TRANSLATIONS_STATES[lang]["clock_mode"]
    try:
        return strings[clock_mode]
    except KeyError:
        print(f"Unknown clock mode code: {clock_mode}")
        return TRANSLATIONS_STATES[lang]["unknown"]


def string_to_clock_mode(string, lang) -> int:
    for code, name in TRANSLATIONS_STATES[lang]["clock_mode"].items():
        if name == string:
            return code
    raise ValueError(f"Invalid clock mode code: {string}")


def clock_source_to_string(clock_source, lang) -> str:
    strings = TRANSLATIONS_STATES[lang]["clock_source"]
    try:
        return strings[clock_source]
    except KeyError:
        print(f"Unknown clock source code: {clock_source}")
        return TRANSLATIONS_STATES[lang]["unknown"]


def string_to_clock_source(string, lang) -> int:
    for code, name in TRANSLATIONS_STATES[lang]["clock_source"].items():
        if name == string:
            return code
    raise ValueError(f"Invalid clock source code: {string}")
