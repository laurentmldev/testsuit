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

"""Requirements coverage: which requirements the tests verify, from any requirements tool.

- ``model``: a requirement and a node of a requirements tree, and the references to them (``[<source>:]<id>[@<version>]``);
- ``backend``: the plugin API (``ReqBackend``, ``register_req_backend()``) and the built-in YAML backend;
- ``sources``: the sources file (``requirements.yml`` at the root of the tests repository) and its backends;
- ``links``: the requirements a test definition and its checks list (``reqs``);
- ``snapshot``: the requirements of a datapack, fetched at generation (``definition/requirements.yml``);
- ``coverage``: coverage of test definitions, datapacks, results and campaigns, and its matrices;
- ``render``: the matrices as text, YAML, CSV or HTML.
"""
from testsuit.reqs.backend import ReqBackend, get_req_backends, register_req_backend, unregister_req_backend
from testsuit.reqs.model import Node, Requirement

__all__ = ["Node", "ReqBackend", "Requirement", "get_req_backends", "register_req_backend", "unregister_req_backend"]
