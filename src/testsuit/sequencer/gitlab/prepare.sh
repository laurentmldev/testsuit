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

# gitlab-runner Custom executor, prepare stage: the sequencer must be reachable ($TSQ_SERVER or tsq.yml).
if ! tsq queue >/dev/null; then
    echo "the sequencer is not reachable (TSQ_SERVER=${TSQ_SERVER:-unset})" >&2
    exit "${SYSTEM_FAILURE_EXIT_CODE:-1}"
fi
echo "sequencer reachable: ${TSQ_SERVER:-from tsq.yml}"
