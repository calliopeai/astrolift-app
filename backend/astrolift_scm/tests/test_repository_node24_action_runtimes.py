"""Keep the repository-owned GitHub workflows off the retired Node 20 runtime."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re


_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
_USES = re.compile(r"^\s*-\s*uses:\s*([^\s#]+)")

_MIGRATED_NODE24_REFS = Counter(
    {
        "actions/checkout@v5": 16,
        "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd": 1,
        "actions/setup-node@v5": 5,
        "actions/setup-python@v6": 6,
        "actions/cache/restore@v5": 6,
        "actions/cache/save@v5": 5,
        "actions/upload-artifact@v6": 1,
        "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f": 1,
        "docker/setup-qemu-action@v4": 1,
        "docker/setup-buildx-action@v4": 2,
        "docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069": 1,
        "docker/login-action@v4": 4,
        "docker/login-action@dbcb813823bdd20940b903addbd779551569679f": 1,
        "docker/build-push-action@v7": 1,
    }
)

_RETIRED_NODE20_REFS = {
    "actions/checkout@v4",
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-node@v4",
    "actions/setup-python@v5",
    "actions/cache/restore@v4",
    "actions/cache/save@v4",
    "actions/upload-artifact@v4",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    "docker/setup-qemu-action@v3",
    "docker/setup-buildx-action@v3",
    "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f",
    "docker/login-action@v3",
    "docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9",
    "docker/build-push-action@v6",
}


def _workflow_action_refs() -> Counter[str]:
    refs: Counter[str] = Counter()
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = _USES.match(line)
            if match:
                refs[match.group(1)] += 1
    return refs


def test_repository_workflows_pin_the_reviewed_node24_action_set() -> None:
    refs = _workflow_action_refs()
    assert Counter({ref: refs[ref] for ref in _MIGRATED_NODE24_REFS}) == _MIGRATED_NODE24_REFS
    assert not (set(refs) & _RETIRED_NODE20_REFS)
