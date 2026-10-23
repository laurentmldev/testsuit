#!/bin/sh
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
