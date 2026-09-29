# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from six import PY2, string_types
from logging import getLogger
logger = getLogger('shadox')

from iced.shadox.v2.exceptions import ShadoxForbiddenException, ShadoxParameterValueTypeException, ShadoxSymbolicPointerPathException


def to_str(s):
    if not s:
        return None
    return s.encode('utf-8') if PY2 else str(s)


class Reference(object):
    '''
    Object coherency will be checked before being pushed
    '''

    def __init__(self, file_path, prefix=None, suffix=None, file_hash=None, is_extres=False):
        """
        Create a reference to an external file by providing its full path.
        You should directly assign the file_path to the Reference parameter value, it will be converted
        """
        if not file_path:
            raise ShadoxForbiddenException('A reference should always have a non-empty file_path')

        self.is_extres = is_extres
        self._file_path = to_str(file_path)
        self._file_hash = to_str(file_hash)
        self.prefix = prefix
        if prefix and suffix and self._suffix != to_str(suffix):
            raise ShadoxForbiddenException("Match failure between file_path {}, prefix {}, and suffix {}".format(file_path, prefix.default_path, suffix))

    @property
    def prefix(self):
        '''Id of the prefix. Possible prefixes are configured on each instance and project by project administrators,
        and can be accessed in the property ShadoxClient.uri_config
        '''
        return self._prefix

    @prefix.setter
    def prefix(self, v):
        if v:
            self._prefix_str, self._suffix = v.decompose_file_path(self._file_path)
            self._prefix = v
        else:
            self._prefix = None
            self._prefix_id = None
            self._suffix = self._file_path

    @property
    def suffix(self):
        '''Part of the path that comes after prefix'''
        return self._suffix

    @suffix.setter
    def suffix(self, v):
        if not v:
            raise ShadoxForbiddenException('suffix cannot be empty')
        if not self.prefix:
            raise ShadoxForbiddenException('Cannot set suffix without a prefix')
        self._suffix = v
        self._file_path = self._prefix_str + self._suffix

    @property
    def file_path(self):
        '''The full path of the resource.
        If prefix is set, equals prefix_url + suffix
        If prefix is not set, equals suffix
        '''
        return self._file_path

    @file_path.setter
    def file_path(self, v):
        '''
        If prefix is set, verify that it matches the new file-path.
        If not, the prefix is erased
        '''
        self._file_path = v
        if self.prefix:
            try:
                self._prefix_str, self._suffix = self.prefix.decompose_file_path(v)
            except ShadoxSymbolicPointerPathException:
                logger.error("New file_path {} does not match the prefix {}: the prefix has been reset".format(v, self.prefix.id))
                self.prefix = None

    @property
    def hash(self):
        '''Free text field, should be used to ensure integrity of data stored outside SHADOX
        '''
        return self._file_hash

    @hash.setter
    def hash(self, v):
        self._file_hash = v

    def __repr__(self):
        if self.prefix:
            return 'prefix={}, suffix={}, file_path={}'.format(
                self._prefix_str, self.suffix, self.file_path
            )
        return 'file_path={}'.format(self.file_path)

    def _to_dict(self):
        if self.is_extres:
            return {'uri': self.file_path, 'hash': self.hash}
        return {'prefixId': self.prefix.id if self.prefix else None, 'suffix': self.suffix, 'filePath': self.file_path, 'hash': self.hash}


class ReferenceValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, key, parameter):
        from iced.shadox.v2.factory.parameters import PARAMETER_TYPE_EXTRES
        is_extres = parameter.definition.structure == PARAMETER_TYPE_EXTRES
        if shadox_value is None:
            return None
        if is_extres:
            if type(shadox_value) is dict:
                return Reference(shadox_value.get('uri'), file_hash=shadox_value.get('hash'), is_extres=True)
            return Reference(shadox_value, is_extres=True)
        elif isinstance(shadox_value, string_types):
            return Reference(shadox_value, None, None, None, is_extres=False)
        elif type(shadox_value) is dict:
            prefixId = shadox_value.get('prefixId')
            prefix = parameter.client.get_uri_config_by_id(prefixId) if prefixId else None
            return Reference(shadox_value.get('filePath'), prefix, shadox_value.get('suffix'), shadox_value.get('hash'), is_extres=False)
        elif type(shadox_value) is Reference:
            return shadox_value

    @staticmethod
    def check(value, parameter, key):
        if value is None or isinstance(value, string_types):
            return True
        if isinstance(value, Reference):
            spps = parameter.client.uri_config.values() # empty list if not implemented

            matching_confs = []
            for spp in spps:
                try:
                    spp.decompose_file_path(value.file_path)
                    matching_confs.append(spp)
                except:
                    pass

            if not value.prefix:
                if value.suffix != value.file_path:
                    raise ShadoxParameterValueTypeException('suffix and file_path should be equal when no prefix is specified')
                if matching_confs:
                    logger.info('Value "{}" matches the following symbolic pointer paths : {}'.format(value.file_path, matching_confs))
                return True
            elif not spps:
                return True
            else:
                if value.prefix not in matching_confs:
                    raise ShadoxParameterValueTypeException('Prefix {} is for parameter {} is not authorised on the server'.format(value.prefix, parameter))
                suffix = value.prefix.decompose_file_path(value.file_path)[1]
                if suffix and suffix != value.suffix:
                    raise ShadoxParameterValueTypeException('expected suffix to be "{}" rather than "{}"'.format(suffix, value.suffix))

            return True
        return False

    @staticmethod
    def format(value, *unused, **kwunused):
        return value._to_dict() if value else None

    @staticmethod
    def format_dataframe(numpy_value, *unused, **kwunused):
        return numpy_value._to_dict() if numpy_value else None
