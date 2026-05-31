"""Tests for the GitLab CI → Astrolift TOML converter."""

import pytest

from astrolift_ci_convert.gitlab import convert, parse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_2JOB = """\
image: python:3.12

stages:
  - build
  - test

variables:
  PYTHONDONTWRITEBYTECODE: "1"
  PIP_NO_CACHE_DIR: "off"

build:
  stage: build
  script:
    - pip install -r requirements.txt
    - python setup.py bdist_wheel

test:
  stage: test
  script:
    - pytest -v
"""

SCRIPT_TO_RUN = """\
image: node:18

stages:
  - install
  - lint
  - test

install_deps:
  stage: install
  script:
    - npm ci

lint:
  stage: lint
  script:
    - npm run lint

test:
  stage: test
  script:
    - npm test
    - npm run coverage
"""

VARIABLES_ENV = """\
variables:
  DATABASE_URL: "postgres://localhost/mydb"
  DEBUG: "false"

stages:
  - build

build:
  stage: build
  variables:
    BUILD_TARGET: production
  script:
    - make build
"""

WITH_BEFORE_AFTER_SCRIPT = """\
image: alpine:latest

before_script:
  - apk add --no-cache curl

after_script:
  - echo "done"

stages:
  - deploy

deploy:
  stage: deploy
  script:
    - ./deploy.sh
"""

CI_VARIABLE_MAPPING = """\
stages:
  - info

info:
  stage: info
  script:
    - echo $CI_COMMIT_SHA
    - echo ${CI_COMMIT_REF_NAME}
    - echo $CI_PIPELINE_ID
    - echo $CI_PROJECT_PATH
"""

NEEDS_EXPLICIT_DAG = """\
stages:
  - build
  - test
  - deploy

build_frontend:
  stage: build
  script: npm run build

build_backend:
  stage: build
  script: make build

test_unit:
  stage: test
  needs: [build_backend]
  script: pytest

deploy_prod:
  stage: deploy
  needs:
    - job: test_unit
    - job: build_frontend
  script: ./deploy.sh
"""

WITH_RULES_MANUAL = """\
stages:
  - release

release:
  stage: release
  when: manual
  script:
    - ./release.sh
"""

WITH_ARTIFACTS = """\
stages:
  - build

build:
  stage: build
  script:
    - make dist
  artifacts:
    paths:
      - dist/
      - build/report.html
"""

WITH_SERVICES = """\
stages:
  - test

test:
  stage: test
  image: python:3.12
  services:
    - postgres:15
    - redis:7
  script:
    - pytest
"""

UNSUPPORTED_FEATURES = """\
stages:
  - build

build:
  stage: build
  parallel:
    matrix:
      - VERSION: [1, 2, 3]
  cache:
    key: $CI_COMMIT_REF_NAME
    paths:
      - .cache/
  trigger:
    project: other/project
  script:
    - make
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSimple2Job:
    def setup_method(self):
        self.pipeline = parse(SIMPLE_2JOB)
        self.toml = convert(self.pipeline)

    def test_two_jobs(self):
        assert len(self.pipeline.jobs) == 2

    def test_job_ids(self):
        ids = [j.job_id for j in self.pipeline.jobs]
        assert "build" in ids
        assert "test" in ids

    def test_global_env(self):
        assert "PYTHONDONTWRITEBYTECODE" in self.pipeline.env

    def test_global_image_as_container(self):
        for job in self.pipeline.jobs:
            assert job.container == "python:3.12"

    def test_stage_dag_implicit_needs(self):
        # test job is in stage 'test' which comes after 'build'
        test_job = next(j for j in self.pipeline.jobs if j.job_id == "test")
        assert "build" in test_job.needs

    def test_script_to_run_step(self):
        build_job = next(j for j in self.pipeline.jobs if j.job_id == "build")
        script_step = next(s for s in build_job.steps if s.name == "script")
        assert "pip install" in script_step.run

    def test_toml_contains_job_tables(self):
        assert "[jobs.build]" in self.toml
        assert "[jobs.test]" in self.toml


class TestScriptToRun:
    def setup_method(self):
        self.pipeline = parse(SCRIPT_TO_RUN)

    def test_three_jobs(self):
        assert len(self.pipeline.jobs) == 3

    def test_multiline_script(self):
        test_job = next(j for j in self.pipeline.jobs if j.job_id == "test")
        script_step = next(s for s in test_job.steps if s.name == "script")
        assert "npm test" in script_step.run
        assert "npm run coverage" in script_step.run

    def test_stage_order_dag(self):
        lint_job = next(j for j in self.pipeline.jobs if j.job_id == "lint")
        # lint is stage 2, depends on install (stage 1)
        assert "install_deps" in lint_job.needs

        test_job = next(j for j in self.pipeline.jobs if j.job_id == "test")
        # test is stage 3, depends on lint (stage 2)
        assert "lint" in test_job.needs


class TestVariablesEnv:
    def setup_method(self):
        self.pipeline = parse(VARIABLES_ENV)
        self.toml = convert(self.pipeline)

    def test_global_variables_in_env(self):
        assert self.pipeline.env.get("DATABASE_URL") == "postgres://localhost/mydb"
        assert self.pipeline.env.get("DEBUG") == "false"

    def test_job_level_variables(self):
        build_job = self.pipeline.jobs[0]
        assert build_job.env.get("BUILD_TARGET") == "production"

    def test_toml_has_env_section(self):
        assert "[env]" in self.toml


class TestBeforeAfterScript:
    def setup_method(self):
        self.pipeline = parse(WITH_BEFORE_AFTER_SCRIPT)

    def test_before_script_as_first_step(self):
        deploy_job = self.pipeline.jobs[0]
        assert deploy_job.steps[0].name == "before_script"
        assert "apk add" in deploy_job.steps[0].run

    def test_after_script_as_last_step(self):
        deploy_job = self.pipeline.jobs[0]
        assert deploy_job.steps[-1].name == "after_script"
        assert "done" in deploy_job.steps[-1].run

    def test_script_in_middle(self):
        deploy_job = self.pipeline.jobs[0]
        script_step = next(s for s in deploy_job.steps if s.name == "script")
        assert "deploy.sh" in script_step.run


class TestCIVariableMapping:
    def setup_method(self):
        self.pipeline = parse(CI_VARIABLE_MAPPING)
        self.toml = convert(self.pipeline)

    def test_sha_mapped(self):
        job = self.pipeline.jobs[0]
        step = next(s for s in job.steps if s.name == "script")
        assert "${ctx.git.sha}" in step.run

    def test_ref_name_mapped(self):
        job = self.pipeline.jobs[0]
        step = next(s for s in job.steps if s.name == "script")
        assert "${ctx.git.ref_name}" in step.run

    def test_pipeline_id_mapped(self):
        job = self.pipeline.jobs[0]
        step = next(s for s in job.steps if s.name == "script")
        assert "${ctx.run.id}" in step.run

    def test_project_path_mapped(self):
        job = self.pipeline.jobs[0]
        step = next(s for s in job.steps if s.name == "script")
        assert "${ctx.repo.full_name}" in step.run

    def test_no_raw_ci_vars_in_toml(self):
        # Original $CI_COMMIT_SHA etc. should be translated
        assert "$CI_COMMIT_SHA" not in self.toml


class TestExplicitNeeds:
    def setup_method(self):
        self.pipeline = parse(NEEDS_EXPLICIT_DAG)

    def test_test_unit_explicit_needs(self):
        test_unit = next(j for j in self.pipeline.jobs if j.job_id == "test_unit")
        assert "build_backend" in test_unit.needs

    def test_deploy_explicit_needs(self):
        deploy = next(j for j in self.pipeline.jobs if j.job_id == "deploy_prod")
        assert "test_unit" in deploy.needs
        assert "build_frontend" in deploy.needs

    def test_build_jobs_no_needs(self):
        build_frontend = next(j for j in self.pipeline.jobs if j.job_id == "build_frontend")
        build_backend = next(j for j in self.pipeline.jobs if j.job_id == "build_backend")
        # First stage — no predecessors
        assert build_frontend.needs == []
        assert build_backend.needs == []


class TestManualTrigger:
    def setup_method(self):
        self.pipeline = parse(WITH_RULES_MANUAL)
        self.toml = convert(self.pipeline)

    def test_dispatch_detected(self):
        assert self.pipeline.on_workflow_dispatch is True

    def test_toml_has_dispatch(self):
        assert "[on.workflow_dispatch]" in self.toml


class TestArtifactsToOutputs:
    def setup_method(self):
        self.pipeline = parse(WITH_ARTIFACTS)
        self.toml = convert(self.pipeline)

    def test_artifacts_in_outputs(self):
        build_job = self.pipeline.jobs[0]
        assert len(build_job.outputs) == 2
        paths = list(build_job.outputs.values())
        assert "dist/" in paths
        assert "build/report.html" in paths


class TestServices:
    def setup_method(self):
        self.pipeline = parse(WITH_SERVICES)

    def test_services_parsed(self):
        test_job = self.pipeline.jobs[0]
        assert len(test_job.services) == 2

    def test_service_images(self):
        test_job = self.pipeline.jobs[0]
        images = {s.image for s in test_job.services}
        assert "postgres:15" in images
        assert "redis:7" in images


class TestUnsupportedFeatures:
    def setup_method(self):
        self.pipeline = parse(UNSUPPORTED_FEATURES)
        self.toml = convert(self.pipeline)

    def test_parallel_matrix_todo(self):
        build_job = self.pipeline.jobs[0]
        assert any("matrix" in t.lower() for t in build_job.todos)

    def test_cache_todo(self):
        build_job = self.pipeline.jobs[0]
        assert any("cache" in t.lower() for t in build_job.todos)

    def test_trigger_todo(self):
        build_job = self.pipeline.jobs[0]
        assert any("trigger" in t.lower() for t in build_job.todos)

    def test_todos_in_toml(self):
        assert "# TODO:" in self.toml
