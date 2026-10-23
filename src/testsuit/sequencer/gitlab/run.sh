#!/bin/sh
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
