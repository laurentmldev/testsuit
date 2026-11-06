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

# gitlab-runner Custom executor, run stage: run the job script given by the runner ($1; $2 is the
# sub-stage name). The job script builds the datapack and calls `tsq submit --wait --follow`: the run log
# becomes the job trace and the verdict its status. TSQ_RUN_FILE records the run id for cleanup.
# A failed run (verdict failed) fails the job as a build failure, anything else as a system failure.
script="$1"
export TSQ_RUN_FILE="${CUSTOM_ENV_CI_BUILDS_DIR:-${TMPDIR:-/tmp}}/.tsq-run-${CUSTOM_ENV_CI_JOB_ID:-$$}"
export TSQ_PRIORITY="${CUSTOM_ENV_TSQ_PRIORITY:-${TSQ_PRIORITY:-}}"
sh "$script"
status=$?
case $status in
    0) exit 0 ;;
    1) exit "${BUILD_FAILURE_EXIT_CODE:-1}" ;;
    *) exit "${SYSTEM_FAILURE_EXIT_CODE:-2}" ;;
esac
