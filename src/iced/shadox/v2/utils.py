#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from .factory import ParametersFactory

from .exceptions import ShadoxForbiddenException

from six import string_types
import datetime, re
import logging

TAG_REGEX = re.compile('(.+):(.+)')
VARNAME_REGEX = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')
PARAM_NAME_REGEX = re.compile(r'^[^/:*%?"<>|•·\\]+$')
PARAM_PATH_REGEX = re.compile(r'^/([^:*%?"<>|•·\\]+/)*$')
PUBLICATION_NAME_REGEX = re.compile(r'^([0-9]+\.){2}[0-9]+(-[a-zA-Z]+(\.[0-9]+)?)?$')

PUBLICATION_LABEL='label'
PUBLICATION_DESCRIPTION='description'
PUBLICATION_SCHEDULED_DATE='scheduled_date'
PUBLICATION_NAME='name'
PUBLICATION_TAGS='tags'
allow_type_list_date=[datetime.date]

VALUE_ALLOWED={
    PUBLICATION_LABEL:'string',
    PUBLICATION_DESCRIPTION:'string',
    PUBLICATION_NAME:'n.m.p-string.z with (n,m,p,z) c N',
    PUBLICATION_SCHEDULED_DATE:'datetime.date and a scheduled date cannot be a prior date of the edition one',
    PUBLICATION_TAGS:'string cluster:tag',
}
VALUE_CHECK = {
    PUBLICATION_LABEL: lambda x: x is None or isinstance(x, string_types),
    PUBLICATION_DESCRIPTION: lambda x: x is None or isinstance(x, string_types),
    PUBLICATION_NAME: lambda x: isinstance(x, string_types) and PUBLICATION_NAME_REGEX.search(x) is not None,
    PUBLICATION_SCHEDULED_DATE: lambda x: x is None or (isinstance(x, datetime.date) and x.year <= 2100 and x.toordinal() >= datetime.date.today().toordinal()),
    PUBLICATION_TAGS: lambda x: True, # Check should be performed upstream, by looking at global project configuration
}

FILE_SUBTYPES = ['BINARY', 'TEXT', 'IMAGE', 'HTML', 'DXML3', 'MARKDOWN']
DOCUMENT_SUBTYPES = ['WORD', 'EXCEL', 'PDF', 'POWERPOINT']

CANNOT_HAVE_VALUE_KIND = ['notebook', 'document']

def check_file_subtype(subtype):
    if subtype not in FILE_SUBTYPES:
        raise ShadoxForbiddenException('File subtype {} is not supported. Use a value among {}'.format(subtype, FILE_SUBTYPES))

def check_document_subtype(type):
    if type not in DOCUMENT_SUBTYPES:
        raise ShadoxForbiddenException('Document subtype {} is not supported. Use a value among {}'.format(type, DOCUMENT_SUBTYPES))

def convert_shadox_to_python(parameter, value, key):
    """
    Convert a shadox parameter value to a python var
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    return ParametersFactory.dispatch(parameter.definition.structure).convert(parameter, value, key)


def check_python_value_match_shadox(parameter, value, key):
    """
    Check that the value python match a shadox structure
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    ParametersFactory.dispatch(parameter.definition.structure).check(parameter, value, key)

def check_python_value_publication_match_shadox(value, key, current_variant, all_publications):
    """
    Check that the value python match a shadox publication structure
    :param publication:
    :param value:
    :param key:
    :return:
    """
    if not VALUE_CHECK[key](value):
        raise ShadoxForbiddenException('Wrong type of value for ' + key + ', expected : ' + VALUE_ALLOWED[key])

    if key == 'name':
        for publication in all_publications:
            if publication.name == value and publication.variant == current_variant:
                raise ShadoxForbiddenException('A publication with version name ' + value + ' already exists on the variant ' + current_variant +' of the dataset')

def parameter_has_file(parameter):
    """
    Look into the parameter definition see if he has a file
    :param parameter:
    :return:
    """
    return ParametersFactory.dispatch(parameter.definition.structure).has_file(parameter)


def has_files_hash_diff(parameter, value, hashes):
    """
    Run through the parameter value to verify if the hash of a file has changed
    :param parameter:
    :param value:
    :param hashes:
    :return:
    """
    return ParametersFactory.dispatch(parameter.definition.structure).has_files_changes(parameter, value, hashes)


def format_value_before_update(parameter, value, hashes, value_key):
    """
    Format the value, only used to upload file atm
    :param parameter:
    :param value:
    :param hashes:
    :param value_key:
    :return:
    """
    return ParametersFactory.dispatch(parameter.definition.structure).format(parameter, value, hashes, value_key)


def hash_parameter_value(parameter, value):
    """
    Hash the parameter value, fix a bug on vector/matrix/dataframe with numpy/pandas representation
    :param parameter:
    :param value:
    :return:
    """
    return ParametersFactory.dispatch(parameter.definition.structure).hash_value(value)


def log_action(message, level_message, level_log):
    """
    Print a log
    :param message:
    :param level_message: 0&1=ERROR,2=WARNING,3=INFO,4+=DEBUG
    :param level_log: between 0 and 5
    :return:
    """
    level_message = max(1, min(4, level_message))
    log = logging.getLogger('shadox')
    if not log.level:
        log.setLevel(-10 * level_log + 50)
    if not log.handlers:
        log.addHandler(logging.StreamHandler())

    # Log levels: 10=DEBUG 20=INFO 30=WARNING 40=ERROR 50=CRITICAL
    log.log(int(-10 * level_message) + 50, message)


def serialize_date(date):
    """
    Change a datetime into a timestamp
    :param date:
    :return:
    """
    if isinstance(date, datetime.datetime):
        return date.isoformat() + 'Z'
    if isinstance(date, datetime.date):
        return date.isoformat() + 'T00:00:00Z'
    return date


def check_tags_is_in_list_project_tags(list_tags, list_project_tags):
    """
    Check that a list of tags are valid (ie are defined as project tags)
    :param list_tags:
    :param list_prokect_tags: dictionary {'clusterName': ['tagName']}
    :return:
    """
    for tag in list_tags:
        match = TAG_REGEX.match(tag)
        if not match:
            raise ShadoxForbiddenException('The tag ' + str(tag) + ' does not match pattern "clusterName:tagName"')

        if not list_project_tags or not match.group(1) in list_project_tags.keys() or not match.group(2) in list_project_tags[match.group(1)]:
            raise ShadoxForbiddenException('The tag ' + str(tag) + ' is not defined as a global/project tag or has been archived')


def validate_tag(tag_name):
    _validate_alphanumeric('Tag', tag_name)


def validate_alias(tag_name):
    _validate_alphanumeric('Alias', tag_name)


def validate_col_title(title):
    _validate_alphanumeric('Column title', title)


def validate_vk_name(vk_name):
    _validate_alphanumeric('Value kind name', vk_name)


def _validate_alphanumeric(name_type, name):
    if not name:
        raise ShadoxForbiddenException(name_type + " name cannot be empty.")
    if not isinstance(name, string_types) or not VARNAME_REGEX.match(name):
        raise ShadoxForbiddenException(name_type + ' name "' + str(name) + '" can only contain alphanumericals and "_", and must start with a letter.')


def validate_param_name(param_name):
    if not param_name or not param_name.strip():
        raise ShadoxForbiddenException(param_name + " name cannot be empty.")
    if not isinstance(param_name, string_types) or not PARAM_NAME_REGEX.match(param_name) or any(char in param_name for char in ['\n', '\a', '\b', '\r', '\f', '\v']):
        raise ShadoxForbiddenException('Parameter name "' + str(param_name) + r'" cannot contain any of the following : \a \b \f \n \r \v / \ " : * % ? < > | • ·')


def validate_path_param(path):
    if not path:
        raise ShadoxForbiddenException(path + " name cannot be empty.")
    if not isinstance(path, string_types) or not PARAM_PATH_REGEX.match(path) or any(char in path for char in ['\n', '\a', '\b', '\r', '\f', '\v']):
        raise ShadoxForbiddenException('Path "' + str(path) + r'" must start and end with a / and cannot contain any of the following : \a \b \f \n \r \v \ " : * % ? < > | • ·')
    if path != '/' and not all([p.strip() for p in path[1:len(path) - 1].split('/')]):
        raise ShadoxForbiddenException("Folders cannot be blank.")

def build_member_name(gname, mname):
    if gname:
        return gname + u' · ' + mname
    return mname

def split_member_name(name):
    if u' • ' in name:
        return name.split(u' • ')
    if u' · ' in name:
        return name.split(u' · ')
    return (None, name)

def is_in_group(name):
    gname, _ = split_member_name(name)
    return gname is not None

def replace_mname_in_aliases(aliases, prev_name, name):
    l = []
    for a in aliases:
        g_a, mname = a.split(u':')
        if not g_a or mname != prev_name:
            # Unexpected alias, ignore it
            l.append(a)
            continue
        l.append(g_a + ':' + name)
    return l

def build_member_aliases(g_aliases, mname):
    return [g_a + u':' + split_member_name(mname)[1] for g_a in g_aliases]


def add_list_items(list_items, items, validator, sortItems=False):
    """
    :param list_items: list in which we want to add items
    :param items: items to add
    :param validator: function to validate items are valid
    :return: the updated list and a boolean if list has changed
    """
    if not isinstance(items, list):
        items = [items]
    new_list = list(list_items)

    # Check the validity of items user want to add
    validator(items)

    added_items = []
    for tag in items:
        if tag not in list_items:
            added_items.append(tag)
            new_list.append(tag)
    if sortItems:
        new_list.sort()
    return new_list, added_items


def delete_list_items(list_items, items=None):
    """
    Delete items from list
    """
    if not items:
        items = list_items
    if items:
        if not isinstance(items, list):
            items = [items]

        items = list(set(items)) # Delete duplicate entry AND duplicate list
        for i in items:
            list_items.remove(i)
    return list_items
