#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from .shadox_file import ShadoxFile


class ShadoxNotebook(ShadoxFile):
    def __init__(self, file_path=None, html_preview=''):
        super(ShadoxNotebook, self).__init__(file_path)
        self._html_preview = html_preview

    @classmethod
    def _init_from_shadox(cls, value_key, shadox_data):
        """INTERNAL USE ONLY - Private method to init ShadoxNotebook from shadox data"""
        notebook = super(ShadoxNotebook, cls)._init_from_shadox(value_key, shadox_data)
        if shadox_data:
            notebook._html_preview = shadox_data['htmlPreview'] if 'htmlPreview' in shadox_data else ''
        return notebook

    def get_html_preview(self):
        return self._html_preview

    def _to_shadox_value(self):
        """INTERNAL USE ONLY - Private method to get the shadox representation of a ShadoxFile"""
        if self._identifier is None:
            return None

        return dict(
            name=self._name,
            size=self._size,
            identifier=self._identifier,
            htmlPreview=self._html_preview
        )
