"""Tests for the GitHub Actions → Astrolift TOML converter."""

import pytest

from astrolift_ci_convert.gha import convert, parse


# ---------------------------------------------------------------------------
# Fixtures — representative real-world GitHub Actions snippets
# ---------------------------------------------------------------------------

SIMPLE_2JOB = """\
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  NODE_ENV: production

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install deps
        run: npm ci
      - name: Build
        run: npm run build

  test:
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: actions/checkout@v3
      - name: Run tests
        run: npm test
"""

DOCKER_BUILD_PUSH = """\
name: Docker

on:
  push:
    tags: ['v*']

jobs:
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          push: "true"
          tags: myimage:latest
"""

MULTI_JOB_MATRIX = """\
name: Matrix CI

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}
      - run: pytest
"""

NODE_CI = """\
name: Node.js CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      - run: npm ci
      - run: npm test
      - uses: actions/upload-artifact@v3
        with:
          name: coverage
          path: coverage/
"""

WORKFLOW_DISPATCH = """\
name: Manual Deploy

on:
  workflow_dispatch:
  schedule:
    - cron: '0 2 * * 0'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Deploy
        run: ./scripts/deploy.sh
        env:
          SHA: ${{ github.sha }}
          REPO: ${{ github.repository }}
"""

CONTEXT_VARS = """\
name: Context vars

on:
  push:
    branches: [main]

jobs:
  info:
    runs-on: ubuntu-latest
    steps:
      - name: Print context
        run: |
          echo ${{ github.sha }}
          echo ${{ github.ref }}
          echo ${{ github.actor }}
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSimple2Job:
    def setup_method(self):
        self.pipeline = parse(SIMPLE_2JOB)
        self.toml = convert(self.pipeline)

    def test_pipeline_name(self):
        assert self.pipeline.name == "CI"

    def test_two_jobs(self):
        assert len(self.pipeline.jobs) == 2

    def test_job_ids(self):
        ids = [j.job_id for j in self.pipeline.jobs]
        assert "build" in ids
        assert "test" in ids

    def test_needs_propagated(self):
        test_job = next(j for j in self.pipeline.jobs if j.job_id == "test")
        assert "build" in test_job.needs

    def test_on_push_branches(self):
        assert "main" in self.pipeline.on_push_branches
        assert "develop" in self.pipeline.on_push_branches

    def test_on_pr_branches(self):
        assert "main" in self.pipeline.on_pull_request_branches

    def test_global_env(self):
        assert self.pipeline.env.get("NODE_ENV") == "production"

    def test_checkout_mapped(self):
        build_job = next(j for j in self.pipeline.jobs if j.job_id == "build")
        checkout_step = build_job.steps[0]
        assert checkout_step.uses == "astrolift/git-checkout@v1"

    def test_runs_on_default(self):
        for job in self.pipeline.jobs:
            assert job.runs_on == "astrolift/default"

    def test_toml_contains_job_ids(self):
        assert "[jobs.build]" in self.toml
        assert "[jobs.test]" in self.toml

    def test_toml_contains_push_trigger(self):
        assert "[on.push]" in self.toml

    def test_toml_contains_pr_trigger(self):
        assert "[on.pull_request]" in self.toml


class TestDockerBuildPush:
    def setup_method(self):
        self.pipeline = parse(DOCKER_BUILD_PUSH)
        self.toml = convert(self.pipeline)

    def test_on_push_tags(self):
        assert "v*" in self.pipeline.on_push_tags

    def test_docker_action_mapped(self):
        job = self.pipeline.jobs[0]
        docker_step = next(s for s in job.steps if s.uses and "docker-build" in s.uses)
        assert docker_step.uses == "astrolift/docker-build@v1"

    def test_with_params_preserved(self):
        job = self.pipeline.jobs[0]
        docker_step = next(s for s in job.steps if s.uses and "docker-build" in s.uses)
        assert "push" in docker_step.with_params

    def test_checkout_also_mapped(self):
        job = self.pipeline.jobs[0]
        checkout_step = job.steps[0]
        assert checkout_step.uses == "astrolift/git-checkout@v1"


class TestMatrixUnsupported:
    def setup_method(self):
        self.pipeline = parse(MULTI_JOB_MATRIX)
        self.toml = convert(self.pipeline)

    def test_matrix_emits_todo(self):
        test_job = self.pipeline.jobs[0]
        assert any("matrix" in t.lower() for t in test_job.todos)

    def test_todo_in_toml_output(self):
        assert "# TODO:" in self.toml
        assert "matrix" in self.toml.lower()

    def test_setup_python_mapped(self):
        test_job = self.pipeline.jobs[0]
        setup_step = next((s for s in test_job.steps if s.uses and "setup-python" in s.uses), None)
        assert setup_step is not None
        assert setup_step.uses == "astrolift/setup-python@v1"


class TestNodeCI:
    def setup_method(self):
        self.pipeline = parse(NODE_CI)
        self.toml = convert(self.pipeline)

    def test_setup_node_mapped(self):
        build_job = self.pipeline.jobs[0]
        node_step = next(s for s in build_job.steps if s.uses and "setup-node" in s.uses)
        assert node_step.uses == "astrolift/setup-node@v1"
        assert node_step.with_params.get("node-version") == "18"

    def test_upload_artifact_mapped(self):
        build_job = self.pipeline.jobs[0]
        artifact_step = next(s for s in build_job.steps if s.uses and "upload-artifact" in s.uses)
        assert artifact_step.uses == "astrolift/upload-artifact@v1"

    def test_run_steps_present(self):
        build_job = self.pipeline.jobs[0]
        run_steps = [s for s in build_job.steps if s.run and not s.run.startswith("#")]
        assert len(run_steps) >= 2  # npm ci + npm test


class TestWorkflowDispatchAndSchedule:
    def setup_method(self):
        self.pipeline = parse(WORKFLOW_DISPATCH)
        self.toml = convert(self.pipeline)

    def test_on_dispatch(self):
        assert self.pipeline.on_workflow_dispatch is True

    def test_on_schedule(self):
        assert len(self.pipeline.on_schedule) == 1
        assert self.pipeline.on_schedule[0].cron == "0 2 * * 0"

    def test_toml_dispatch(self):
        assert "[on.workflow_dispatch]" in self.toml

    def test_toml_schedule(self):
        assert "[[on.schedule]]" in self.toml
        assert "0 2 * * 0" in self.toml


class TestContextVariables:
    def setup_method(self):
        self.pipeline = parse(CONTEXT_VARS)
        self.toml = convert(self.pipeline)

    def test_sha_translated(self):
        job = self.pipeline.jobs[0]
        step = job.steps[0]
        assert "${ctx.git.sha}" in step.run

    def test_ref_translated(self):
        job = self.pipeline.jobs[0]
        step = job.steps[0]
        assert "${ctx.git.ref}" in step.run

    def test_actor_translated(self):
        job = self.pipeline.jobs[0]
        step = job.steps[0]
        assert "${ctx.actor}" in step.run

    def test_no_raw_gha_expressions_in_toml(self):
        # ${{ }} should not appear verbatim in output
        assert "${{" not in self.toml


class TestUnknownAction:
    """Unknown uses: entries are converted to run with comment + TODO."""

    YAML = """\
name: Unknown action

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: some-org/some-action@v2
"""

    def setup_method(self):
        self.pipeline = parse(self.YAML)
        self.toml = convert(self.pipeline)

    def test_todo_emitted(self):
        job = self.pipeline.jobs[0]
        step = job.steps[0]
        assert len(step.todos) > 0 or step.run is not None

    def test_toml_references_original(self):
        assert "some-org/some-action" in self.toml
