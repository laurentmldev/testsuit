# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import warnings
import re

import numpy
from . import NoneType
from ...exceptions import ShadoxForbiddenException
ALLOWED_TYPES = [int, float, numpy.float64, NoneType]


class DoubleValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, *unused):
        if shadox_value is None:
            return shadox_value
        float_value = float(shadox_value)
        if not numpy.isnan(float_value):
            if DoubleValue.sigdig_count(float_value) > 15:
                warnings.warn('Number {} has more than 15 significant digit, which is now forbidden in Shadox. To push it back to Shadox, you will need to set your client\'s sigdig_mode to "truncate" or "round"'.format(float_value), RuntimeWarning)
        return float_value

    @staticmethod
    def check(value, *unused):
        return type(value) in ALLOWED_TYPES

    @staticmethod
    def format(value, parameter, _unused1, vk_name, *unused, **kwunused):
        if value is not None and not numpy.isnan(value):
            sigdig_mode = parameter.client.sigdig_mode
            if DoubleValue.sigdig_count(value) > 15:
                parameter_name =  parameter.alias if parameter.alias else parameter.path + parameter.name
                if sigdig_mode == "truncate":
                    truncated_value = DoubleValue.truncate_to_15_sigdig(value)
                    warnings.warn("Value kind {} of parameter {} has been truncated to {}".format(vk_name, parameter_name, truncated_value), RuntimeWarning, stacklevel=4)
                    return truncated_value
                elif sigdig_mode == "round":
                    rounded_value = DoubleValue.round_to_15_sigdig(value)
                    warnings.warn("Value kind {} of parameter {} has been rounded to {}".format(vk_name, parameter_name, rounded_value), RuntimeWarning, stacklevel=4)
                    return rounded_value
                else:
                    raise ShadoxForbiddenException('Invalid double with >15 significant digits in {}'.format(parameter_name))

        return value

    @staticmethod
    def format_dataframe(numpy_value, *unused, **kwunused):
        return DoubleValue.format(float(numpy_value), *unused, **kwunused)

    @staticmethod
    def round_to_15_sigdig(float_value):
        if float_value == 0:
            return 0
        return round(float_value, -int(numpy.floor(numpy.log10(numpy.abs(float_value)))) + 14)

    @staticmethod
    def sigdig_count(float_value):
        if float_value==0:
            return 0
        #Use __repr__() because str() has a weird behavior on Python2
        trimmed_value = float_value.__repr__().replace('.', '').split('e')[0]
        pos = re.search(r'[1-9](?:\d*[1-9])?', trimmed_value).span()
        return pos[1] - pos[0]


    @staticmethod
    def truncate_to_15_sigdig(float_value):
        if float_value==0:
            return 0
        else:
            abs_value = numpy.abs(float_value)
            exponent = 14 - numpy.floor(numpy.log10(abs_value))
            return numpy.sign(float_value)*numpy.floor(abs_value*(10**exponent))/(10**exponent)
