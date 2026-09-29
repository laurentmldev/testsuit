#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.


class ShadoxInternalException(Exception):
    pass


class ShadoxMissingParametersException(Exception):
    pass


class ShadoxUnauthorizedException(Exception):
    pass


class ShadoxAPIKeyRevokedException(Exception):
    pass


class ShadoxAPIKeyExpiredException(Exception):
    pass


class ShadoxForbiddenException(Exception):
    pass


class ShadoxNotFoundException(Exception):
    pass


class ShadoxParameterNotFoundException(Exception):
    pass


class ShadoxParameterValueNotFoundException(Exception):
    pass


class ShadoxParameterValueTypeException(Exception):
    pass


class ShadoxFileNotFoundException(Exception):
    pass
