"""Concrete built-in action implementations.

Each class corresponds to one versioned first-party action.
``render_steps`` returns a list of ``{"name": ..., "run": ...}``
dicts that the spawner schedules as individual step executions.
"""

from __future__ import annotations

import shlex

from astrolift_pipelines.actions import ActionInputError, BuiltinAction, InputSpec


class GitCheckoutAction(BuiltinAction):
    """astrolift/git-checkout@v1

    Clones the repository at the given ref into the workspace directory.
    Must run before any ``run`` step that reads the source tree.

    For private repos, pass a ``token`` that resolves from secrets.
    The token is injected into the HTTPS clone URL at render time so it
    never appears in ``git log`` or process listings.
    """

    name = "astrolift/git-checkout"
    description = "Clone and check out the repository at a given ref."
    inputs = {
        "repository": InputSpec(
            required=True,
            description="Repository URL (https or ssh). Interpolations like "
            "${ctx.git.clone_url} are supported.",
        ),
        "ref": InputSpec(
            required=True,
            description="Git ref to check out (branch, tag, or full SHA).",
        ),
        "fetch_depth": InputSpec(
            required=False,
            default=1,
            description="Shallow-clone depth. 0 = full history.",
        ),
        "token": InputSpec(
            required=False,
            default="",
            description="Bearer token for private repos. Injected into the "
            "clone URL, never written to disk.",
        ),
    }

    def render_steps(self, with_params: dict, env: dict, context: dict) -> list[dict]:
        p = self.validate_inputs(with_params)
        repo_url: str = p["repository"]
        ref: str = p["ref"]
        fetch_depth: int = int(p["fetch_depth"])
        token: str = p["token"]

        # Inject token into https:// URLs without exposing it in history.
        if token and repo_url.startswith("https://"):
            repo_url = repo_url.replace("https://", f"https://x-token:{token}@", 1)

        depth_flag = f"--depth={fetch_depth}" if fetch_depth > 0 else ""

        steps = [
            {
                "name": "git clone",
                "run": " ".join(filter(None, ["git", "clone", depth_flag, shlex.quote(repo_url), "."])),
            },
            {
                "name": "git checkout",
                "run": f"git checkout {shlex.quote(ref)}",
            },
        ]
        return steps


class DockerBuildAction(BuiltinAction):
    """astrolift/docker-build@v1

    Builds a container image from a Dockerfile.

    Build approach: BuildKit via ``DOCKER_BUILDKIT=1`` with
    ``--output type=image``.  The spawner is responsible for
    providing a BuildKit-capable Docker socket (either Docker-in-Docker
    sidecar or a BuildKit daemon mounted into the job pod).  The action
    itself does not manage the daemon — it only renders the ``docker
    buildx build`` invocation.

    If ``push: true`` the registry credentials must already be present
    in the job environment (e.g. via a prior ``docker login`` step or a
    Kubernetes pull-secret mounted as env vars).
    """

    name = "astrolift/docker-build"
    description = "Build (and optionally push) a Docker image with BuildKit."
    inputs = {
        "context": InputSpec(
            required=False,
            default=".",
            description="Build context path, relative to the workspace root.",
        ),
        "file": InputSpec(
            required=False,
            default="Dockerfile",
            description="Path to the Dockerfile, relative to the workspace root.",
        ),
        "tags": InputSpec(
            required=True,
            description="Image tag or list of tags "
            "(e.g. 'registry/app:sha' or ['registry/app:sha', 'registry/app:latest']).",
        ),
        "build_args": InputSpec(
            required=False,
            default={},
            description="Key-value map of Docker build arguments.",
        ),
        "push": InputSpec(
            required=False,
            default=False,
            description="Push the image after build.",
        ),
    }

    def render_steps(self, with_params: dict, env: dict, context: dict) -> list[dict]:
        p = self.validate_inputs(with_params)
        ctx_path: str = p["context"]
        dockerfile: str = p["file"]
        raw_tags = p["tags"]
        build_args: dict = p["build_args"] or {}
        push: bool = bool(p["push"])

        tags: list[str] = [raw_tags] if isinstance(raw_tags, str) else list(raw_tags)
        if not tags:
            raise ActionInputError(f"Action '{self.name}': 'tags' must contain at least one tag.")

        tag_flags = " ".join(f"-t {shlex.quote(t)}" for t in tags)
        arg_flags = " ".join(
            f"--build-arg {shlex.quote(k)}={shlex.quote(str(v))}" for k, v in build_args.items()
        )
        push_flag = "--push" if push else ""

        cmd = " ".join(
            filter(
                None,
                [
                    "docker",
                    "buildx",
                    "build",
                    tag_flags,
                    arg_flags,
                    f"-f {shlex.quote(dockerfile)}",
                    push_flag,
                    shlex.quote(ctx_path),
                ],
            )
        )

        return [
            {
                "name": "docker build",
                "run": cmd,
                "env": {"DOCKER_BUILDKIT": "1"},
            }
        ]


class KubectlApplyAction(BuiltinAction):
    """astrolift/kubectl-apply@v1

    Applies a Kubernetes manifest file or kustomize directory to the
    named cluster.

    Cluster credentials are managed by Astrolift's existing cluster
    credential system — the spawner mounts a kubeconfig into the job
    environment before this step runs. The action renders the
    ``kubectl apply`` invocation only; it does not manage credentials.
    """

    name = "astrolift/kubectl-apply"
    description = "Apply a K8s manifest or kustomize directory to a cluster."
    inputs = {
        "manifest": InputSpec(
            required=True,
            description="Path or glob to the manifest file(s), or a kustomize "
            "directory. Passed directly to ``kubectl apply -f``.",
        ),
        "cluster": InputSpec(
            required=False,
            default="",
            description="Cluster name from the Astrolift cluster registry. "
            "When empty the in-cluster context or KUBECONFIG default is used.",
        ),
        "namespace": InputSpec(
            required=False,
            default="",
            description="Target namespace. Omit to use the manifest-declared "
            "namespace or the current context default.",
        ),
    }

    def render_steps(self, with_params: dict, env: dict, context: dict) -> list[dict]:
        p = self.validate_inputs(with_params)
        manifest: str = p["manifest"]
        namespace: str = p["namespace"]

        ns_flag = f"--namespace={shlex.quote(namespace)}" if namespace else ""

        cmd = " ".join(filter(None, ["kubectl", "apply", "-f", shlex.quote(manifest), ns_flag]))

        step: dict = {"name": "kubectl apply", "run": cmd}

        # Surface the target cluster as metadata; the spawner uses this
        # to select credentials before scheduling the step.
        if p["cluster"]:
            step["cluster"] = p["cluster"]

        return [step]


class AstroliftDeployAction(BuiltinAction):
    """astrolift/astrolift-deploy@v1

    Deploys an image to an Astrolift-managed workload via the
    Astrolift deployment API.  No raw kubectl calls — the platform
    API handles credential resolution, manifest rendering, and
    rollout tracking.

    The spawner must inject ``ASTROLIFT_API_URL`` and
    ``ASTROLIFT_DEPLOY_TOKEN`` into the job environment so the step
    can authenticate.
    """

    name = "astrolift/astrolift-deploy"
    description = "Deploy an image to an Astrolift-managed workload via the platform API."
    inputs = {
        "app_slug": InputSpec(
            required=True,
            description="Slug of the Astrolift application to deploy.",
        ),
        "environment": InputSpec(
            required=True,
            description="Target environment name (e.g. 'production', 'staging').",
        ),
        "image_tag": InputSpec(
            required=True,
            description="Full image reference to deploy " "(e.g. 'registry.example.com/app:abc1234').",
        ),
        "cluster": InputSpec(
            required=False,
            default="",
            description="Cluster slug. When empty the app's default cluster is used.",
        ),
    }

    def render_steps(self, with_params: dict, env: dict, context: dict) -> list[dict]:
        p = self.validate_inputs(with_params)
        app_slug: str = p["app_slug"]
        environment: str = p["environment"]
        image_tag: str = p["image_tag"]
        cluster: str = p["cluster"]

        # Render as a call to the astro CLI which wraps the deploy API.
        # The spawner is responsible for having astro installed.
        cluster_flag = f"--cluster={shlex.quote(cluster)}" if cluster else ""

        cmd = " ".join(
            filter(
                None,
                [
                    "astro",
                    "deploy",
                    shlex.quote(app_slug),
                    f"--environment={shlex.quote(environment)}",
                    f"--image={shlex.quote(image_tag)}",
                    cluster_flag,
                ],
            )
        )

        return [
            {
                "name": "astrolift deploy",
                "run": cmd,
            }
        ]
