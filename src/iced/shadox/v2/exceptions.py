#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.


class ShadoxBaseException(Exception):
    pass


class ShadoxInternalException(ShadoxBaseException):
    pass


class ShadoxMissingParametersException(ShadoxBaseException):
    pass


class ShadoxUnauthorizedException(ShadoxBaseException):
    pass


class ShadoxAPIKeyRevokedException(ShadoxBaseException):
    pass


class ShadoxAPIKeyExpiredException(ShadoxBaseException):
    pass


class ShadoxForbiddenException(ShadoxBaseException):
    pass


class ShadoxNotFoundException(ShadoxBaseException):
    pass


class ShadoxParameterNotFoundException(ShadoxBaseException):
    pass


class ShadoxParameterValueNotFoundException(ShadoxBaseException):
    pass


class ShadoxParameterValueTypeException(ShadoxBaseException):
    pass


class ShadoxFileNotFoundException(ShadoxBaseException):
    pass


class ShadoxNoSnapshotException(ShadoxBaseException):
    pass

class ShadoxSymbolicPointerPathException(ShadoxBaseException):
    pass
