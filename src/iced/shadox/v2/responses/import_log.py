#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.


class ImportLog(object):
  '''Log of all the operations done on parameters
  '''
  def __init__(self, json_data, client, import_type, filename):
    self._json = json_data
    self._client = client
    self._import_type = import_type
    self._filename = filename
    self._failed = json_data['failed']
    self._parameters = [ParameterLog(p) for p in json_data['parameterLogs']]

  @property
  def failed(self):
    '''True if no modifications were saved, either because of some errors or flag commit=False'''
    return self._failed

  @property
  def parameters(self):
    '''List of ParameterLog that were not skipped
    '''
    return [pl for pl in self._parameters if pl.operation != 'SKIP']

  @property
  def all(self):
    '''All ParameterLogs
    '''
    return self._parameters

  @property
  def with_errors(self):
    '''List of ParameterLog containing at least one error'''
    return [pl for pl in self._parameters if any(m.status == 'ERROR' for m in pl.messages)]

  def __repr__(self):
    oks = self.parameters
    errs = self.with_errors
    n_skipped = len(self._parameters) - len(oks) - len(errs)
    ret = 'ImportLog '
    if self._failed:
      ret += ' NO MODIFICATION SAVED'
    if errs:
      ret += ' ' + repr(errs)
    if n_skipped:
      ret += ' (' + str(n_skipped) + ' skipped without error)'
    if oks:
      ret += ' ' + repr(oks)
    return ret

  def _write_as_text(self):
    ret = ''
    for plog in self._parameters:
      n_err = 0
      n_warn = 0
      n_info = 0
      for m in plog.messages:
        if m.status == 'ERROR': n_err += 1
        if m.status == 'WARNING': n_warn += 1
        if m.status == 'INFO' or m.status == 'OK': n_info += 1

      ret += '[{}]<PARAMETER{}> {} ({} errors, {} warnings, {} info)\n'.format(
        plog.operation, (' - line: ' + str(plog._line)) if plog._line else '', plog.name, n_err, n_warn, n_info
      )
      for m in plog.messages:
        ret += '\t' + '[{}] {}\n'.format(m.status, m.message)
      ret += '\n'
    return ret

  def export_to_file(self, file_path=None):
    ret = self._write_as_text()
    if not file_path:
      file_path = self._client._prepare_temp_dir('imports_' + self._import_type + '/')
      file_path += self._filename + '.txt'
    with open(file_path, 'wb') as file_log:
      file_log.write(ret.encode('utf8'))
    return file_path

class ParameterLog(object):
  def __init__(self, json_data):
    self._path = json_data['path'] or '/'
    self._name = json_data['name']
    self._operation = json_data['operation']
    self._messages = [Log(m) for m in json_data['messages']]
    self._columns = [ColumnLog(c) for c in json_data['columns'] or []]
    self._line = json_data['line'] or None

  @property
  def path(self):
    return self._path

  @property
  def name(self):
    return self._name

  @property
  def operation(self):
    '''CREATE, UPDATE, SUBSCRIBE, ADD_DEPENDENCY, SKIP'''
    return self._operation

  @property
  def messages(self):
    return self._messages

  @property
  def columns(self):
    '''Only for dataframes'''
    return self._columns

  @property
  def line_number(self):
    '''For CSV import, line of the parameter'''
    return self._line

  def __repr__(self):
    return 'ParameterLog ' + self.operation + ' ' + self.path + self.name + ' ' + repr(self.messages)

class ColumnLog(object):
  def __init__(self, json_data):
    self._name = json_data['name']
    self._messages = [Log(m) for m in json_data['messages']]
    self._line = json_data['line'] or None

  @property
  def name(self):
    '''Column name'''
    return self._name

  @property
  def messages(self):
    '''List of Logs for this column'''
    return self._messages

  @property
  def line_number(self):
    '''For CSV import, line of the parameter'''
    return self._line


class Log(object):
  def __init__(self, json_data):
    self._status = json_data['status']
    self._message = json_data['message']

  @property
  def status(self):
    '''OK, INFO, WARNING, ERROR'''
    return self._status

  @property
  def message(self):
    return self._message

  def __repr__(self):
    return self.status + ' - ' + self.message
