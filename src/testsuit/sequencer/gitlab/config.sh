#!/bin/sh
# gitlab-runner Custom executor, config stage (sequencer README, section 13): where the builds go.
# The jobs run on the runner host itself; TSQ_BUILDS_DIR overrides the default folder.
builds="${TSQ_BUILDS_DIR:-$HOME/.tsq-gitlab/builds}"
cat <<JSON
{"builds_dir": "$builds", "cache_dir": "${TSQ_CACHE_DIR:-$HOME/.tsq-gitlab/cache}",
 "builds_dir_is_shared": false,
 "driver": {"name": "testsuit sequencer", "version": "1"}}
JSON
