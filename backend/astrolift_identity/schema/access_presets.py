"""GraphQL projection of the Easy-mode access presets (#2272)."""

import strawberry

from astrolift_identity.access_presets import PRESETS, TASKS, role_tasks


@strawberry.type(name="AstroliftAccessTask")
class AccessTaskType:
    key: str
    label: str
    granted: bool


@strawberry.type(name="AstroliftAccessPresetRole")
class AccessPresetRoleType:
    scope_level: str
    role_slug: str
    tasks: list[AccessTaskType]


@strawberry.type(name="AstroliftAccessPreset")
class AccessPresetType:
    key: str
    label: str
    summary: str
    roles: list[AccessPresetRoleType]


def access_presets() -> list[AccessPresetType]:
    labels = {task.key: task.label for task in TASKS}
    return [
        AccessPresetType(
            key=preset.key,
            label=preset.label,
            summary=preset.summary,
            roles=[
                AccessPresetRoleType(
                    scope_level=scope,
                    role_slug=slug,
                    tasks=[
                        AccessTaskType(key=key, label=labels[key], granted=granted)
                        for key, granted in role_tasks(slug).items()
                    ],
                )
                for scope, slug in preset.roles
            ],
        )
        for preset in PRESETS
    ]
