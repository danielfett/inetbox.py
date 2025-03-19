from decimal import Decimal


def bool_to_int(value):
    return int(value)


def int_to_bool(value):
    return bool(value)


# convert two-byte representation of temperature to a Decimal
def temp_code_to_decimal(bytestring) -> str:
    if bytestring == 0xAAA or bytestring == 0xAAAA or bytestring == 0x0000:
        return "0"
    return str((Decimal(bytestring) / Decimal(10) - Decimal(273)).quantize(Decimal("0.1")))


# convert two-byte representation of temperature to a str
def temp_code_to_string(bytestring) -> str:
    return str(temp_code_to_decimal(bytestring))


# inverse function of the above, observe exception for None
def decimal_to_temp_code(decimal) -> int:
    if decimal is None or decimal < Decimal("5"):
        # return 0xAAA
        return 0x00
    return int((decimal + Decimal(273)) * Decimal(10))


def string_to_temp_code(string):
    return decimal_to_temp_code(Decimal(string))


# error status is 1 if a warning exists, rest of values unknown yet
def operating_status_to_string(operating_status):
    if operating_status == 0:
        return "Off"
    elif operating_status == 1:
        return "WARNING"
    elif operating_status == 4:
        return "On (starting)"
    elif operating_status == 5:
        return "On"
    else:
        return f"UNKNOWN ({operating_status})"


# error code is two bytes, first byte * 100 + second byte is the error code
def error_code_to_string(error_code_bytes):
    error_code = error_code_bytes[1] * 100 + error_code_bytes[0]
    return str(error_code)

# Electric heating power level is stored as a two-byte integer and has
# the values 0, 900, or 1800
def el_power_code_to_string(el_power_code):
    return str(el_power_code)

# inverse of the above
def string_to_el_power_code(string):
    code = int(string)
    if code == 0 or code == 900 or code == 1800:
        return code
    else:
        raise ValueError(f"Invalid electric heating power code: {code}")

# energy mix is stored in a byte, with the lowest bit indicating
# whether gas is used and the second lowest bit indicating whether
# electricity is used
energy_mix_mapping = {
    0b00: "none",
    0b01: "gas",
    0b10: "electricity",
    0b11: "mix",
}

def energy_mix_code_to_string(energy_mix_code):
    return energy_mix_mapping[energy_mix_code]

# inverse of the above
def string_to_energy_mix_code(string):
    for code, name in energy_mix_mapping.items():
        if name == string:
            return code
    raise ValueError(f"Invalid energy mix code: {string}")


def heating_mode_to_string(heating_mode):
    if heating_mode == 0:
        return "off"
    elif heating_mode == 1:
        return "eco"
    elif heating_mode == 10:
        return "high"
    else:
        return f"UNKNOWN ({heating_mode})"

# inverse of the above
def string_to_heating_mode(string):
    if string == "off":
        return 0
    elif string == "eco":
        return 1
    elif string == "high":
        return 10
    else:
        raise ValueError(f"Invalid heating mode: {string}")

def clock_mode_to_string(clock_mode):
    if clock_mode == 0:
        return "24h"
    elif clock_mode == 1:
        return "12h"
    else:
        return f"UNKNOWN ({clock_mode})"
    
def string_to_clock_mode(string):
    if string.startswith("24"):
        return 0
    else:
        return 1
    
def clock_source_to_string(clock_source):
    if clock_source == 1:
        return "manual"
    elif clock_source == 2:
        return "inetbox"
    else:
        return f"UNKNOWN ({clock_source})"
    
def string_to_clock_source(string):
    if string == "manual":
        return 1
    else:
        return 2