# gitlab-runner Custom executor for the sequencer

These scripts turn a sequencer host into a GitLab runner (sequencer README, section 13): the job
builds its datapack and submits it; the run log becomes the job trace, the verdict the job status.

`/etc/gitlab-runner/config.toml` (paths: `python -c "import testsuit.sequencer, os; print(os.path.dirname(testsuit.sequencer.__file__))"`):

```toml
[[runners]]
  name = "toytrain lab"
  executor = "custom"
  environment = ["TSQ_SERVER=http://127.0.0.1:8640"]
  [runners.custom]
    config_exec = "sh"
    config_args = ["<sequencer>/gitlab/config.sh"]
    prepare_exec = "sh"
    prepare_args = ["<sequencer>/gitlab/prepare.sh"]
    run_exec = "sh"
    run_args = ["<sequencer>/gitlab/run.sh"]
    cleanup_exec = "sh"
    cleanup_args = ["<sequencer>/gitlab/cleanup.sh"]
```

`.gitlab-ci.yml` of a test repository (runner tags can name stage types):

```yaml
front_contact:
  tags: [toytrain]
  variables: {TSQ_PRIORITY: "60"}
  script:
    - datapack testdef/front_contact/testdef.yml -o out/
    - tsq submit out/*.zip --follow
  after_script:
    - tsq files "$(cat "$TSQ_RUN_FILE")" --results --get    # the results zip, as a job artifact
  artifacts:
    when: always
    paths: [out/, "*_results.zip"]
```

`tsq submit` reads `TSQ_PRIORITY` when `--priority` is not given, and writes the run id to
`TSQ_RUN_FILE`, so that `cleanup.sh` cancels the run when the job is cancelled. Exit codes: 0
succeeded, 1 failed (build failure), 2 error (system failure).
