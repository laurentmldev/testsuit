#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import os
import six
import numpy
from copy import deepcopy
import xml.sax.saxutils as saxutils

from wand.image import Image
from docxtpl import DocxTemplate, InlineImage
from jinja2 import Environment,meta
from docx.shared import Mm
from .shadoxdocx import replace_bad_characters

class XMLTooltipHelper(object):
    def __init__(self, snapshot):
        self.tooltip = snapshot._client.snapshot_path.replace("project/","").replace("/dataset/"," - ").replace("/snapshot/"," - ")

    def sdt(self, content):
        return "<w:sdt><w:sdtPr><w:alias w:val=\"" \
            + self.tooltip \
            + """\"/><w:lock w:val="sdtContentLocked"/></w:sdtPr><w:sdtEndPr/><w:sdtContent><w:r><w:t>""" \
            + content \
            + "</w:t></w:r></w:sdtContent></w:sdt>"

    def p(self, content, indent = 0):
        return "<w:p>" + \
            ("<w:pPr><w:ind w:left='" + str(320 * indent) + "'/></w:pPr>" if indent > 0 else "") \
            + self.sdt(content) \
            + "</w:p>"


class ShadoxDocxTemplate(DocxTemplate):

    def get_shadox_variables(self, jinja_env=None):
        xml = self.get_xml()
        xml = self.patch_xml(xml)

        if not jinja_env:
            jinja_env = Environment()
        parsed_template = jinja_env.parse(xml)
        return meta.find_undeclared_variables(parsed_template)

class WordParameter(object):

    def __init__(self, parameter, tag):
        self.parameter = parameter
        self.helper = XMLTooltipHelper(self.parameter.snapshot)
        self.filters = self.gives_filename_and_precision()
        self.units = ""
        self.alignment = "center"
        self.width = 50
        self.value = None
        self.tag = tag
        self.style = "TableGrid"
        self.column = None
        self.query = None
        self.setindex = None
        self.loc = None
        self.filename_columns = []
        self.round_columns = {}
        self.dump_xml = None
        self.name = None
        self.description = None

    def get_precision(self):
        try:
            return self.parameter.definition['contentType']['precision']
        except KeyError:
            return None


    def gives_filename_and_precision(self):
        if self.parameter.get_structure() != 'dataframe' and self.parameter.get_type() == 'file':
            return ") |filename"
        elif self.get_precision() != None:
            return ") |round(" + str(-self.get_precision()) + ") "
        else:
            return  ")"

    def do_round(self, precision, columns):
        if self.get_precision() != None:
            self.filters = self.filters.replace("|round(" + str(-self.get_precision()) + ") ",  " | round(" + str(precision) + ")")
        else:
            self.filters+= " | round(" + str(precision) + ")"

    def do_unit(self):
        self.units = self.parameter.get_unit()
        if self.dump_xml == None:
            self.dump_xml = ""

    def do_alignment(self, alignment):
        self.alignment = alignment

    def do_width(self, width):
        self.width = width

    def do_raw(self, columns):
        self.filters = self.filters.replace(" |filename","")
        if self.get_precision() != None:
            self.filters = self.filters.replace("|round(" + str(-self.get_precision()) + ") ", "")

    def do_value(self,key):
        self.value = key

    def get_value(self):
        if self.value is None:
            return self.parameter.first_value
        else:
            return self.parameter.value_kinds[self.value].value

    def get_xml_escaped_value(self):
        return self.get_xml()

    def do_style(self, style):
        self.style = style

    def do_name(self):
        self.name = self.parameter.name
        if self.dump_xml == None:
            self.dump_xml = ""

    def do_description(self):
        self.description = self.parameter.get_description()
        if self.dump_xml == None:
            self.dump_xml = ""

    def do_add(self, number):
        self.filters = "+" + str(number) + self.filters

    def do_substract(self, number):
        self.filters = "-" + str(number) + self.filters

    def do_multiply(self, number):
        self.filters = "*" + str(number) + self.filters

    def do_divide(self, number):
        self.filters = "/" + str(number) + self.filters

    def do_dump(self):
        self.dump_xml = self.helper.p("Name: " + self.parameter.name)
        self.dump_xml += self.helper.p("Description: " + self.parameter.get_description())
        self.dump_xml += self.helper.p("Structure: " + self.parameter.get_structure())

    def do_origin(self):
        self.dump_xml = self.helper.p("Name: " + self.parameter.name)
        self.dump_xml += self.helper.p("Imported from dataset: " + self.parameter.origin.dataset_path)
        self.dump_xml += self.helper.p("Published at: " + str(self.parameter.origin.publication_date))


class WordScalar(WordParameter):
    def __init__(self, parameter, tag):
        WordParameter.__init__(self, parameter, tag)
        self.xml1 = "{{ ("
        self.xml2 = " }}"

    def do_dump(self):
        WordParameter.do_dump(self)
        self.dump_xml += self.helper.p("Type: " + self.parameter.get_type())
        self.dump_xml += self.helper.p("Unit: " + self.parameter.get_unit())
        self.dump_xml += self.helper.p("Values: ")
        for vk in self.parameter.value_kinds:
            self.dump_xml += self.helper.p(vk.name + ": " + str(vk.preview), indent = 1)

    def get_xml_escaped_value(self):
        val = self.get_value()
        if isinstance(val, six.string_types):
            return saxutils.escape(val)
        return val

    def __str__(self):
        if self.dump_xml == None:
            return self.helper.sdt(self.xml1  + str(self.tag) + self.filters  + self.xml2 + self.units)
        elif self.name != None:
            return self.helper.sdt(self.name)
        elif self.description != None:
            return self.helper.sdt(self.description)
        elif self.units != "":
            return self.helper.sdt(self.units)
        else:
            return self.dump_xml


class WordScalarFile(WordScalar):
    def __init__(self, parameter, tag,  tpl, bad_characters):
        WordScalar.__init__(self, parameter, tag)
        self.tpl = tpl
        self.bad_characters = bad_characters
        self.name = str(self.get_value()).partition(" - ")[0]
        self.suffix = self.name.partition(".")[-1]
        self.xml = ""
        self.is_shown = False
        self.download_path = self.name
        self.wants_path = False


    def do_show(self, height):          #Depending on the file, provides the xml to show it
        self.is_shown = True
        path = self.get_value().download()
        if self.suffix == "docx":
            self.xml = DocxTemplate(path).get_xml()
        elif self.suffix in ("png", "jpeg", "jpg"):
            self.xml = str(InlineImage(self.tpl, path, height=Mm(height))) # delegates to _insert_image()
        if self.suffix == "pdf":
            with Image(filename=path) as img:
                new_path = path[:-3] + "png"
                img.save(filename=new_path)
            self.xml = str(InlineImage(self.tpl, new_path, height=Mm(height)))
            os.remove(new_path)

    def do_download(self, path):
        self.download_path = replace_bad_characters(path, self.bad_characters) + "/" + self.name
        self.get_value().download(self.download_path)

    def do_filepath(self):
        self.wants_path = True

    def do_dump(self):
        WordParameter.do_dump(self)
        self.dump_xml += self.helper.p("Type: " + self.parameter.get_type())
        self.dump_xml += self.helper.p("Subtype: " + (self.parameter.definition['contentType']['subtype'] if self.parameter.definition['contentType'] != None else 'None'))
        self.dump_xml += self.helper.p("Values: ")
        for vk in self.parameter.value_kinds:
            self.dump_xml += self.helper.p(vk.name + ": " + (str(vk.preview['name']) + " - size:" + str(vk.preview['size']) if vk.preview != None else 'None'), indent = 1)

    def __str__(self):
        if self.dump_xml == None :
            if self.is_shown:
                return  self.helper.p(self.xml)
            elif self.wants_path:
                return  self.helper.p(self.download_path)
            else:
                return  self.helper.p(self.xml1 + str(self.tag) + self.filters + self.xml2)
        elif self.name != None:
            return self.helper.sdt(self.name)
        elif self.description != None:
            return self.helper.sdt(self.description)
        elif self.units != "":
            return self.helper.sdt(self.units)
        else:
            return self.dump_xml

class WordVector(WordParameter):
    def __init__(self, parameter, tag):
        WordParameter.__init__(self, parameter, tag)
        self.xml1 = """<w:tbl><w:tblPr><w:tblStyle w:val=\""""
        self.xml2 = """"/><w:jc w:val=\""""
        self.xml3 = """"/><w:tblBorders><w:top w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:start w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:end w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:insideH w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:insideV w:val="single" w:sz="6" w:space="0" w:color="000000" /></w:tblBorders><w:tblW w:type="pct" w:w=\""""
        self.xml4 = """%"/></w:tblPr><w:tblGrid><w:gridCol w:w="9200"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tr for item in """
        self.xml5 = """.tolist() %}"""
        self.xml6 = """</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr>"""
        self.xml7 = """ {{ (item """
        self.xml8 = """ }} """
        self.xml9 = """</w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tr endfor %}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"""

    def do_dump(self):
        WordParameter.do_dump(self)
        self.dump_xml += self.helper.p("Type: " + self.parameter.get_type())
        self.dump_xml += self.helper.p("Unit: " + self.parameter.get_unit())
        self.dump_xml += self.helper.p("Values: ")
        for vk in self.parameter.value_kinds:
            self.dump_xml += self.helper.p(vk.name + ": " + (("dimensions: " + str(vk.preview['dimensions'])) if vk.preview != None else 'None'), indent = 1)

    def __str__(self):
        if self.dump_xml == None:
            return  self.xml1 + self.style + self.xml2 + self.alignment + self.xml3 + str(self.width) + self.xml4 + str(self.tag) + self.xml5 +  self.xml6 + self.helper.sdt(self.xml7 + self.filters + self.xml8 + self.units) + self.xml9
        elif self.name != None:
            return self.helper.sdt(self.name)
        elif self.description != None:
            return self.helper.sdt(self.description)
        elif self.units != "":
            return self.helper.sdt(self.units)
        else:
            return self.dump_xml

    def get_xml_escaped_value(self):
        val = self.get_value()
        return numpy.array([saxutils.escape(v) if isinstance(v, six.string_types) else v for v in val])

    def tolist(self):
        value = deepcopy(self.get_value())
        return value.tolist()

class WordMatrix(WordParameter):
    def __init__(self, parameter, tag):
        WordParameter.__init__(self, parameter, tag)
        self.xml1 = """<w:tbl><w:tblPr><w:tblStyle w:val=\""""
        self.xml2 = """"/><w:jc w:val=\""""
        self.xml3 = """"/><w:tblW w:type="pct" w:w=\""""
        self.xml4 = """%"/><w:tblBorders><w:top w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:start w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:end w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:insideH w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:insideV w:val="single" w:sz="6" w:space="0" w:color="000000" /></w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="2224"/><w:gridCol w:w="896"/><w:gridCol w:w="1536"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tr for item in """
        self.xml5 = """.tolist() %}"""
        self.xml6 = """</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc for cell in item %}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr>"""
        self.xml7 = """ {{ (cell """
        self.xml8 = """ }} """
        self.xml9 = """</w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc endfor %}</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tr endfor %}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"""

    def do_dump(self):
        WordParameter.do_dump(self)
        self.dump_xml += self.helper.p("Type: " + self.parameter.get_type())
        self.dump_xml += self.helper.p("Unit: " + self.parameter.get_unit())
        self.dump_xml += self.helper.p("Values: ")
        for vk in self.parameter.value_kinds:
            self.dump_xml += self.helper.p(vk.name + ": " + "dimensions: " + str(vk.preview['dimensions'] if vk.preview != None else 'None'), indent = 1)

    def get_xml_escaped_value(self):
        val = self.get_value()
        for idx, v in numpy.ndenumerate(val):
            if isinstance(v, six.string_types):
                val[idx] = saxutils.escape(v)
        return val

    def __str__(self):
        if self.dump_xml == None:
            return self.xml1 + self.style + self.xml2 + self.alignment + self.xml3 + str(self.width) + self.xml4 + str(self.tag) + self.xml5 + self.xml6 + self.helper.sdt(self.xml7 + self.filters + self.xml8 + self.units) + self.xml9
        elif self.name != None:
            return self.helper.sdt(self.name)
        elif self.description != None:
            return self.helper.sdt(self.description)
        elif self.units != "":
            return self.helper.sdt(self.units)
        else:
            return self.dump_xml

    def tolist(self):
        value = deepcopy(self.get_value())
        return value.tolist()

class WordDataFrame(WordParameter):
    def __init__(self, parameter, tag):
        WordParameter.__init__(self, parameter, tag)
        self.xml1 = """<w:tbl>"""+"""<w:tblPr><w:tblStyle w:val=\""""
        self.xml2 = """"/><w:jc w:val=\""""
        self.xml3 = """"/><w:tblW w:type="pct" w:w=\""""
        self.xml4 = """%"/><w:tblBorders><w:top w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:start w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:end w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:insideH w:val="single" w:sz="6" w:space="0" w:color="000000" /><w:insideV w:val="single" w:sz="6" w:space="0" w:color="000000" /></w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="2931"/><w:gridCol w:w="951"/><w:gridCol w:w="1536"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc for title in """ + str(self.tag) + """titles %}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{{ title }}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc endfor %}</w:t></w:r></w:p></w:tc></w:tr>"""
        self.xml5 = """<w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/>></w:pPr><w:r><w:t>{%tr for item in """
        self.xml6 = """.values.tolist() %}</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc for cell in item %}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr>"""
        self.xml7 = """ {{ (cell """
        self.xml8 = """ }} """
        self.xml9 = """</w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc endfor %}</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tr endfor %}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"""
        self.units = ""
        self.titles = self.parameter.get_df_columns_titles()
        self.units_value = self.parameter.get_df_columns_units()
        # everything could be found in definition : title, type, unit,...
        all_col_and_types_and_defs = zip(self.parameter.get_df_columns_titles(), self.parameter.get_df_columns_types(), [definition['contentType'] for definition in self.parameter.definition['columns']])
        self.filename_columns = [column for column,type,_ in all_col_and_types_and_defs if type == "file"]
        self.round_columns = dict([[column, ctype['precision']] for column,_,ctype in all_col_and_types_and_defs if 'precision' in ctype])

    def do_unit(self):
        self.units += """<w:tr><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc for unit in """ + str(self.tag) + """units %}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr>""" + self.helper.sdt("""{{ unit }}""") + """</w:p></w:tc><w:tc><w:tcPr><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{%tc endfor %}</w:t></w:r></w:p></w:tc></w:tr>"""

    def do_column(self,columns):
        self.column = columns

    def do_query(self, formula):
        self.query = formula

    def do_setindex(self,keys):
        self.setindex = keys

    def do_loc(self, label):
        self.loc = label

    def do_raw(self, columns):
        for column in columns:
            try:                                        #either we want to have the raw name for a file
                self.filename_columns.remove(column)
            except ValueError:                          #or the raw value for a number
                try:
                    del self.round_columns[column]
                except KeyError:
                    pass

    def do_round(self, precision, columns):
        for column in columns:
            self.round_columns[column] = precision

    def get_xml_escaped_value(self):
        val = self.get_value()
        return val.applymap(lambda v: saxutils.escape(v) if isinstance(v, six.string_types) else v)

    def do_dump(self):
        WordParameter.do_dump(self)
        self.dump_xml += self.helper.p("Columns: ")
        titles = self.parameter.get_df_columns_titles()
        types = self.parameter.get_df_columns_types()
        units = self.parameter.get_df_columns_units()
        i = 0
        while i < len(titles):
            self.dump_xml += self.helper.p("Title: " + titles[i] + ": ", indent = 1)
            self.dump_xml += self.helper.p("Type: " + types[i], indent = 2)
            self.dump_xml += self.helper.p("Unit: " + units[i], indent = 2)
            i += 1
        self.dump_xml += self.helper.p("Values: ")
        for vk in self.parameter.value_kinds:
            self.dump_xml += self.helper.p(vk.name + ": " + "dimensions: " + str(vk.preview['dimensions'] if vk.preview != None else 'None'), indent = 1)

    def __str__(self):
        if self.dump_xml == None:
            return self.xml1 + self.style + self.xml2 + self.alignment + self.xml3 + str(self.width) + self.xml4 + self.units + self.xml5 + str(self.tag) + self.xml6  + self.helper.sdt(self.xml7 + str(self.filters) + self.xml8) + self.xml9
        elif self.name != None:
            return self.helper.sdt(self.name)
        elif self.description != None:
            return self.helper.sdt(self.description)
        else:
            return self.dump_xml

    def tolist(self):
        value = deepcopy(self.get_value())
        return value.values.tolist()

class WordSnapshotData():

    def __init__(self, snapshot, client):
        self.client = client
        self.snapshot = snapshot
        self.helper = XMLTooltipHelper(self.client.snapshot)

    def __str__(self):
        return self.helper.sdt(str(self.snapshot))

class WordPublication():

    def __init__(self, publication, client):
        self.publication = publication
        self.client = client
        self.helper = XMLTooltipHelper(self.client.snapshot)

    def __str__(self):
        if self.publication:
            text = 'Publication ' + self.publication.name + ((' - ' + self.publication.label) if self.publication.label else '')
        else:
            text = 'No publication attached to snapshot'
        return self.helper.sdt(text)


