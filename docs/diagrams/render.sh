#!/bin/sh
# Renders every diagram of this folder to SVG: sh docs/diagrams/render.sh [path/to/plantuml.jar]
# (PlantUML 1.2025 or later; no Graphviz needed, the diagrams use the smetana layout.)
set -e
cd "$(dirname "$0")"
java -jar "${1:-${PLANTUML_JAR:-plantuml.jar}}" -tsvg ./*.puml
