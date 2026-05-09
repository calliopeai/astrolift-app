"""Tests for manifest auto-discovery (#119).

The discoverer is a pure function over a `{path: contents}` map, so
the tests don't need fixtures — each one is a tight signal/inference
pair. Coverage focuses on the columns of the spec table so a
regression that drops a runtime mapping is loud.
"""

from __future__ import annotations

from astrolift_manifest.discover import infer_manifest_from_signals

# ---- runtime detection ------------------------------------------------


def test_python_runtime_via_pipfile():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"Pipfile": None},
    )
    assert any("python" in h for h in out.hints)


def test_node_runtime_default_port_3000():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"package.json": None},
    )
    assert out.manifest["workloads"][0]["containers"][0]["port"] == 3000


def test_django_app_overrides_python_to_8000():
    """When manage.py is present alongside Pipfile, Django wins —
    the runtime stays 'python' but the default port flips to 8000."""
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"Pipfile": None, "manage.py": None},
    )
    assert out.manifest["workloads"][0]["containers"][0]["port"] == 8000


def test_go_runtime_default_port_8080():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"go.mod": None},
    )
    assert out.manifest["workloads"][0]["containers"][0]["port"] == 8080


def test_unknown_runtime_falls_back_to_8080_with_low_confidence():
    out = infer_manifest_from_signals(app_name="hello", files={})
    assert out.confidence == "low"
    assert "unknown" in out.hints[0]


# ---- Dockerfile signals ----------------------------------------------


def test_single_dockerfile_yields_one_public_workload():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile": "FROM python:3.12\nEXPOSE 8000\n",
            "Pipfile": None,
        },
    )
    workloads = out.manifest["workloads"]
    assert len(workloads) == 1
    assert workloads[0]["is_public"] is True
    assert workloads[0]["containers"][0]["port"] == 8000
    assert workloads[0]["containers"][0]["dockerfile_path"] == "Dockerfile"


def test_expose_port_overrides_runtime_default():
    """The Dockerfile's EXPOSE wins over the runtime default — it's
    the strongest signal we have for the actual listen port."""
    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile": "FROM node:20\nEXPOSE 9090\n",
            "package.json": None,
        },
    )
    assert out.manifest["workloads"][0]["containers"][0]["port"] == 9090


def test_dockerfile_without_expose_uses_runtime_default():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile": "FROM node:20\n",
            "package.json": None,
        },
    )
    assert out.manifest["workloads"][0]["containers"][0]["port"] == 3000


def test_multiple_dockerfiles_first_is_public():
    """Per the spec, the first Dockerfile (alphabetical order) wins
    public; the rest go private."""
    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile.api": "FROM python:3.12\nEXPOSE 8000\n",
            "Dockerfile.worker": "FROM python:3.12\n",
            "Pipfile": None,
        },
    )
    workloads = out.manifest["workloads"]
    assert len(workloads) == 2
    public = [w for w in workloads if w["is_public"]]
    assert len(public) == 1
    assert public[0]["name"] == "hello-api"


def test_dockerfile_jobs_yields_jobs_block():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile": "FROM python:3.12\nEXPOSE 8000\n",
            "Dockerfile.jobs": "FROM python:3.12\n",
            "Pipfile": None,
        },
    )
    assert "jobs" in out.manifest
    assert out.manifest["jobs"][0]["dockerfile_path"] == "Dockerfile.jobs"


# ---- env hints --------------------------------------------------------


def test_env_example_keys_surfaced_as_hints():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile": "FROM python:3.12\nEXPOSE 8000\n",
            "Pipfile": None,
            ".env.example": "DATABASE_URL=postgres://...\nREDIS_URL=redis://...\nDEBUG=False\n",
        },
    )
    assert "_discovered_env_keys" in out.manifest
    assert out.manifest["_discovered_env_keys"] == [
        "DATABASE_URL",
        "REDIS_URL",
        "DEBUG",
    ]
    assert any("DATABASE_URL" in h for h in out.hints)


def test_env_keys_dedupe_and_skip_lowercase():
    """Lowercase or non-shell-style keys aren't picked up — we want
    real env vars, not user-defined comments."""
    text = "FOO=1\nfoo=2\nBAR=3\nFOO=already-seen\n"
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"package.json": None, ".env.example": text},
    )
    assert out.manifest["_discovered_env_keys"] == ["FOO", "BAR"]


# ---- managed services from compose -----------------------------------


def test_compose_postgres_drafts_managed_service():
    compose = """
    services:
      web:
        image: my/app
      db:
        image: postgres:16
    """
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"package.json": None, "docker-compose.yml": compose},
    )
    kinds = [m["kind"] for m in out.manifest.get("managed_services", [])]
    assert "postgres" in kinds


def test_compose_redis_and_postgres_both_drafted():
    compose = """
    services:
      cache:
        image: redis:7
      db:
        image: postgres:16
      app:
        image: my/app
    """
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"package.json": None, "docker-compose.yml": compose},
    )
    kinds = sorted(m["kind"] for m in out.manifest["managed_services"])
    assert kinds == ["postgres", "redis"]


def test_compose_with_no_known_images_drafts_nothing():
    compose = """
    services:
      web:
        image: my/app
    """
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"package.json": None, "docker-compose.yml": compose},
    )
    assert out.manifest.get("managed_services", []) == []


# ---- confidence -------------------------------------------------------


def test_confidence_high_when_dockerfile_plus_runtime_agree():
    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile": "FROM python:3.12\nEXPOSE 8000\n",
            "Pipfile": None,
        },
    )
    assert out.confidence == "high"


def test_confidence_low_when_only_runtime_signal():
    """Runtime signal alone is a guess — surface low confidence so
    the UI shows the draft as speculative."""
    out = infer_manifest_from_signals(
        app_name="hello",
        files={"package.json": None},
    )
    assert out.confidence == "low"


# ---- output shape -----------------------------------------------------


def test_output_shape_round_trips_through_parser_when_minimal():
    """The discovered manifest should be parser-acceptable for the
    common case (single Dockerfile). We strip our private
    ``_discovered_env_keys`` key before parsing."""
    import tomli_w

    from astrolift_manifest.parser import parse_raw

    out = infer_manifest_from_signals(
        app_name="hello",
        files={
            "Dockerfile": "FROM python:3.12\nEXPOSE 8000\n",
            "Pipfile": None,
        },
    )
    payload = {k: v for k, v in out.manifest.items() if not k.startswith("_")}
    raw_toml = tomli_w.dumps(payload)
    parsed = parse_raw(raw_toml)
    assert parsed.name == "hello"
    assert len(parsed.workloads) == 1
    assert parsed.workloads[0].containers[0].port == 8000
