# Copyright (C) 2026 Laurent Millet-Lacombe
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of testsuit.
# testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
# General Public License as published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
# testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
# Public License for more details.
# You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
# see <https://www.gnu.org/licenses/>.

"""Toy train: an example test environment built on testsuit.

- filemgrs: readers for the toy-train data files (new formats and a CSV syntax)
- criteria, checks: mexploit criteria for the toy-train tests
- importer: datapack importer for the toy-train release store
- plugin: registers all of the above in testsuit, and the report logo (assets/toytrain_logo.svg)
- cli: the toytrain command
"""
