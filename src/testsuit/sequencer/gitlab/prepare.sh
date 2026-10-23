#!/bin/sh
# gitlab-runner Custom executor, prepare stage: the sequencer must be reachable ($TSQ_SERVER or tsq.yml).
if ! tsq queue >/dev/null; then
    echo "the sequencer is not reachable (TSQ_SERVER=${TSQ_SERVER:-unset})" >&2
    exit "${SYSTEM_FAILURE_EXIT_CODE:-1}"
fi
echo "sequencer reachable: ${TSQ_SERVER:-from tsq.yml}"
