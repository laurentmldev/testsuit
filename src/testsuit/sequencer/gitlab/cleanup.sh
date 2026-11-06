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

# gitlab-runner Custom executor, cleanup stage: cancel the job's run if it is still queued or running
# (the job was cancelled or timed out).
run_file="${CUSTOM_ENV_CI_BUILDS_DIR:-${TMPDIR:-/tmp}}/.tsq-run-${CUSTOM_ENV_CI_JOB_ID:-$$}"
[ -f "$run_file" ] || exit 0
run=$(cat "$run_file")
rm -f "$run_file"
state=$(tsq show "$run" 2>/dev/null | sed -n 's/^state: //p')
case "$state" in
    done|cancelled|"") ;;
    *) tsq cancel "$run" ;;
esac
exit 0
