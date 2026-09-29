#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import re

from copy import deepcopy
from jinja2 import Environment
from .shadoxdocx import ShadoxDocxHelper, replace_bad_characters
from .shadoxdocxtpl import ShadoxDocxTemplate, WordScalar, WordVector, WordMatrix, WordDataFrame, WordParameter, WordScalarFile, WordSnapshotData, WordPublication
from tempfile import NamedTemporaryFile
from time import strftime
from uuid import uuid4


class MakeWordException(Exception):
    pass


class TagParser(object):
    def __init__(self, clients):
        self.clients = clients
        self.multi_clients = isinstance(clients, dict)

    def extract_request_uid(self, tag, bad_characters):
        tag = replace_bad_characters(tag, bad_characters)[1:-1]  # get the shadox request
        m = re.search("\D", tag)  # id of the request to differentiate them
        uid = "A" + tag[:m.start()]

        return tag[m.start():], uid

    def extract_client(self, request):
        if self.multi_clients:  # MakeWord initialized with multiple clients
            re_find_client = re.compile(r'^(\S+)::(.*)$')
            match = re_find_client.match(request)
            if not match:
                raise MakeWordException("Shadox Client selector missing in the following request: " + request)
            else:
                client_name, request = match.group(1), match.group(2)
                try:
                    return request, self.clients[client_name]
                except KeyError:
                    raise MakeWordException("Unknown Shadox client selector (" + client_name + ") in the following request: " + request)
        else:  # MakeWord initialized with one client
            if re.match(r'\S+::', request):
                raise MakeWordException("Unexpected Shadox client selector in the following request: " + request)
            else:
                return request, self.clients


class MakeWord:
    def __init__(self, clients):
        self.clients = clients
        self.tag_parser = TagParser(self.clients)

    def process_shadox_tag(
        self,
        tag,
        bad_characters,
        wordparameters,
        context,
        tpl,
        context_tpl
    ):
        request, uid = self.tag_parser.extract_request_uid(tag, bad_characters)
        request, client = self.tag_parser.extract_client(request)

        if re.match("(path|id|alias)", request):  # Parameter request
            parameter = client.get_parameter(request)
            wordparameter = WordParameter(parameter, uid)  # depending on the structure of the parameter, calls for the right wordparameter
            if parameter.get_structure() == 'scalar':  # and puts in the context the structure which fits docxtemplate
                if parameter.get_type() == 'file':
                    wordparameter = WordScalarFile(parameter, uid, tpl, bad_characters)
                else:
                    wordparameter = WordScalar(parameter, uid)
                context_tpl[tag] = wordparameter
            elif parameter.get_structure() == 'vector':
                wordparameter = WordVector(parameter, uid)
                context_tpl[tag] = wordparameter
            elif parameter.get_structure() == 'matrix':
                wordparameter = WordMatrix(parameter, uid)
                context_tpl[tag] = wordparameter
            elif parameter.get_structure() == 'dataframe':
                wordparameter = WordDataFrame(parameter, uid)
                context_tpl[tag] = wordparameter
                context[wordparameter.tag + "units"] = wordparameter.units_value
                context[wordparameter.tag + "titles"] = wordparameter.titles
            wordparameters.append(wordparameter)

        elif request == 'client':
            context_tpl[tag] = client

        elif request == 'snapshot':
            context_tpl[tag] = client.snapshot

        elif request == 'publication':
            try: # if a publication is attached, otherwise it goes to except
                context_tpl[tag] = WordPublication(self.clients.get_current_publication(), client)
            except:
                context_tpl[tag] = WordPublication(None, client)

        else:  # request information on the snapshot
            switch = {
                "dataset_path": client.dataset_path,
                "snapshot_path": client.snapshot_path,
                "snapshot_variant": client.snapshot.variant,
                "snapshot_version": client.snapshot.version
            }
            snapshot = switch.get(request, "Information not found")
            wordsnapshotdata = WordSnapshotData(snapshot, client)
            context_tpl[tag] = wordsnapshotdata

    def make(self, path_tpl, path_doc):
        doc = ShadoxDocxHelper(path_tpl)
        shadox_open_tag = "A" + str(uuid4().int)  # The shadox open tag "<" must start with a letter for jinja2 templating to be able to recognize it
        shadox_close_tag = str(uuid4().int)
        bad_characters = {
            "-": str(uuid4().int),
            "/": str(uuid4().int),
            ":": str(uuid4().int),
            " · ": str(uuid4().int),
            "<": shadox_open_tag,
            ">": shadox_close_tag,
            " ": str(uuid4().int),
        }
        doc.make_right_for_template(bad_characters)
        tempdoc = NamedTemporaryFile(suffix=".docx")  # Make the template that fits ours needs
        doc.save(tempdoc)

        tag_env_filters = {
            'round': do_round_tag,
            'unit': do_unit_tag,
            'alignment': do_alignment_tag,
            'width': do_width_tag,
            'show': do_show_tag,
            'raw': do_raw_tag,
            'value': do_value_tag,
            'style': do_style_tag,
            'download': do_download_tag,
            'filepath': do_filepath_tag,
            'name': do_name_tag,
            'description': do_description_tag,
            'dump': do_dump_tag,
            'columns': do_columns_tag,
            'query': do_query_tag,
            'setindex': do_setindex_tag,
            'loc': do_loc_tag,
            'add': do_add_tag,
            'substract': do_substract_tag,
            'multiply': do_multiply_tag,
            'divide': do_divide_tag,
            'origin': do_origin_tag,
        }
        shadox_jinja_tag_env = Environment()  # Environment for the first rendering, with style filters and filters who are pushed to the next template
        shadox_jinja_tag_env.filters.update(tag_env_filters)

        env_filters = {
            'round': do_round,
            'filename': do_filename,
        }
        shadox_jinja_env = Environment()  # Environment for the final rendering with filter on the values we want to have
        shadox_jinja_env.filters.update(env_filters)

        tpl = ShadoxDocxTemplate(tempdoc)  # Import our template
        context_tpl = {}
        context = {}
        tags = tpl.get_shadox_variables(shadox_jinja_tag_env)  # Get all undeclared variables from our template
        wordparameters = []

        for tag in tags:
            # separate tags for which we will need to fetch things on Shadox and the other ones
            if tag.startswith(shadox_open_tag) and tag.endswith(shadox_close_tag):
                self.process_shadox_tag(tag, bad_characters, wordparameters, context, tpl, context_tpl)

        context_tpl["time"] = strftime("%A %d %B %Y %H:%M:%S")  # request which doesn't come from shadox
        tpl.render(context_tpl, shadox_jinja_tag_env)  # Render our second template, with the correct structure to render the final document
        for wordparameter in wordparameters:
            value = deepcopy(wordparameter.get_xml_escaped_value())  # Gets in the right value

            if wordparameter.filename_columns != []:
                for column in wordparameter.filename_columns:
                    value[column] = value[column].apply(do_filename)

            if wordparameter.round_columns != {}:
                for key, val in wordparameter.round_columns.items():
                    if val == 0:
                        do_df_round = lambda x: int(x)
                    elif val > 0:
                        do_df_round = lambda x: round(x, val)
                    else:
                        do_df_round = lambda x: int(round(x, val))
                    value[key] = value[key].apply(do_df_round)

            if wordparameter.column is not None:  # Changes things when we have filters for dataframes coming from pandas function
                titles = []
                units = []
                to_keep = []

                for title in wordparameter.column:
                    to_keep += [value.columns.get_loc(title)]

                for elt in to_keep:
                    units.append(wordparameter.units_value[elt])
                    titles.append(wordparameter.titles[elt])

                value = value.filter(items=wordparameter.column)
                context[wordparameter.tag + "units"] = units
                context[wordparameter.tag + "titles"] = titles

            if wordparameter.query is not None:
                value = value.query(wordparameter.query)

            if wordparameter.setindex is not None:
                value = value.set_index(wordparameter.setindex, drop=False)
                to_switch = wordparameter.titles.index(wordparameter.setindex)
                titles = context[wordparameter.tag + "titles"]
                units = context[wordparameter.tag + "units"]
                titles[0], titles[to_switch] = titles[to_switch], titles[0]
                units[0], units[to_switch] = units[to_switch], units[0]
                value = value.reindex(columns=titles)
                context[wordparameter.tag + "units"] = units
                context[wordparameter.tag + "titles"] = titles

            if wordparameter.loc is not None:
                value = value.loc[wordparameter.loc, ]
            context[wordparameter.tag] = value
        tpl.render(context, shadox_jinja_env)  # Render our final document
        tpl.save(path_doc)
        print("Your document has been created!")


def do_round_tag(wordparameter, precision=0, columns=None):
    if type(wordparameter) == float:
        return do_round(wordparameter, precision)
    else:
        wordparameter.do_round(precision, columns)
        return wordparameter


def do_unit_tag(wordparameter):
    wordparameter.do_unit()
    return wordparameter


def do_alignment_tag(wordparameter, alignment):
    wordparameter.do_alignment(alignment)
    return wordparameter


def do_width_tag(wordparameter, width):
    wordparameter.do_width(width)
    return wordparameter


def do_show_tag(wordparameter, height):
    wordparameter.do_show(height)
    return wordparameter


def do_value_tag(wordparameter, key=None):
    wordparameter.do_value(key)
    return wordparameter


def do_download_tag(wordparameter, path):
    wordparameter.do_download(path)
    return wordparameter


def do_filepath_tag(wordparameter):
    wordparameter.do_filepath()
    return wordparameter


def do_round(value, precision=0):
    try:
        value = float(value)
        if precision == 0:
            return int(round(value))
        elif precision > 0:
            return round(value, precision)
        else:
            return int(round(value, precision))
    except ValueError:
        return value


def do_raw_tag(wordparameter, columns=None):
    wordparameter.do_raw(columns)
    return wordparameter


def do_filename(value):
    value = str(value).partition(" - ")[0]
    return value


def do_style_tag(wordparameter, style):
    wordparameter.do_style(style)
    return wordparameter


def do_name_tag(wordparameter):
    wordparameter.do_name()
    return wordparameter


def do_description_tag(wordparameter):
    wordparameter.do_description()
    return wordparameter


def do_dump_tag(wordparameter):
    wordparameter.do_dump()
    return wordparameter


def do_columns_tag(wordparameter, columns):
    wordparameter.do_column(columns)
    return wordparameter


def do_query_tag(wordparameter, formula):
    wordparameter.do_query(formula)
    return wordparameter


def do_setindex_tag(wordparameter, keys):
    wordparameter.do_setindex(keys)
    return wordparameter


def do_loc_tag(wordparameter, label):
    wordparameter.do_loc(label)
    return wordparameter


def do_add_tag(wordparameter, number):
    wordparameter.do_add(number)
    return wordparameter


def do_substract_tag(wordparameter, number):
    wordparameter.do_substract(number)
    return wordparameter


def do_multiply_tag(wordparameter, number):
    wordparameter.do_multiply(number)
    return wordparameter


def do_divide_tag(wordparameter, number):
    wordparameter.do_divide(number)
    return wordparameter


def do_origin_tag(wordparameter):
    wordparameter.do_origin()
    return wordparameter
