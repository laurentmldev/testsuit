#!/bin/sh
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

# Renders every diagram of this folder to SVG: sh docs/diagrams/render.sh [path/to/plantuml.jar]
# (PlantUML 1.2025 or later; no Graphviz needed, the diagrams use the smetana layout.)
set -e
cd "$(dirname "$0")"
java -jar "${1:-${PLANTUML_JAR:-plantuml.jar}}" -tsvg ./*.puml
