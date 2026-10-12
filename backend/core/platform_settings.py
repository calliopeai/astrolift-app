"""Install settings the platform operator edits in the web admin (#1861).

Every Constance key is either editable here or listed with the reason it is
not: a credential stays in the Django admin, and a public feature flag is
switched on Administration > Features (``setFeatureFlag``), which has its own
admission rules. A test holds every key in ``CONSTANCE_CONFIG`` to one of the
three, so a new setting cannot slip in unclassified.

Values are cleaned by the form field the Django admin uses for the key
(``constance.forms.FIELDS`` plus ``CONSTANCE_ADDITIONAL_FIELDS``), so the
two paths accept and refuse the same input. Groups are
``CONSTANCE_CONFIG_FIELDSETS``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from django import forms
from django.conf import settings

# Key -> why it is not edited here.
DJANGO_ONLY: dict[str, str] = {
    "GOOGLE_PLAY_INTEGRITY_API_KEY": "A credential: set it in the Django admin, never sent to a browser.",
}

FEATURES_PAGE_REASON = "A feature flag: switch it under Administration > Features."

# Key -> the warning shown before a change, which must be confirmed.
DANGEROUS: dict[str, str] = {
    "AGENT_POD_UID": "Agent images must own this uid. A uid they do not own breaks every non-root agent pod.",
    "AGENT_POD_GID": "Agent images must own this gid. A gid they do not own breaks every non-root agent pod.",
    "AGENT_NETWORK_FENCE": "Changes what every agent pod on every cluster can reach.",
    "REQUIRE_STEP_UP_AUTH": "Turning this off lets sensitive actions run without a fresh sign-in.",
    "REQUIRE_ATTESTATION_FOR_SENSITIVE_OPS": "Turning this off lets unattested devices run sensitive actions.",
}

_KINDS: tuple[tuple[type[forms.Field], str], ...] = (
    (forms.BooleanField, "bool"),
    (forms.ChoiceField, "choice"),
    # FloatField and DecimalField subclass IntegerField, so they come first.
    (forms.FloatField, "float"),
    (forms.DecimalField, "decimal"),
    (forms.IntegerField, "int"),
    (forms.CharField, "string"),
)


class PlatformSettingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclasses.dataclass(frozen=True)
class PlatformSetting:
    key: str
    group: str
    kind: str
    """``bool``, ``choice``, ``int``, ``float``, ``decimal`` or ``string``."""
    help_text: str
    value: str
    default: str
    choices: tuple[str, ...]
    editable: bool
    read_only_reason: str
    warning: str

    @property
    def changed(self) -> bool:
        return self.value != self.default


def _feature_flag_keys() -> frozenset[str]:
    from core.schema.types.server_info import _PUBLIC_FEATURE_FLAGS

    return frozenset(constance_key for constance_key, _public, _description in _PUBLIC_FEATURE_FLAGS)


def _read_only_reason(key: str) -> str:
    if key in DJANGO_ONLY:
        return DJANGO_ONLY[key]
    if key in _feature_flag_keys():
        return FEATURES_PAGE_REASON
    return ""


def _field(key: str) -> forms.Field:
    """The form field the Django admin builds for ``key``."""
    from constance.forms import FIELDS

    options = settings.CONSTANCE_CONFIG[key]
    config_type = options[2] if len(options) == 3 else type(options[0])
    field_class, kwargs = FIELDS[config_type]
    return field_class(**kwargs)


def _kind(field: forms.Field) -> str:
    for field_class, kind in _KINDS:
        if isinstance(field, field_class):
            return kind
    return ""


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _groups() -> dict[str, str]:
    return {
        key: group
        for group, fieldset in settings.CONSTANCE_CONFIG_FIELDSETS.items()
        for key in fieldset["fields"]
    }


def _setting(key: str, group: str) -> PlatformSetting:
    from constance import config

    options = settings.CONSTANCE_CONFIG[key]
    field = _field(key)
    kind = _kind(field)
    reason = _read_only_reason(key) or ("" if kind else "This type is edited in the Django admin.")
    secret = key in DJANGO_ONLY
    choices = tuple(str(value) for value, _label in getattr(field, "choices", ())) if kind == "choice" else ()
    return PlatformSetting(
        key=key,
        group=group,
        kind=kind or "string",
        help_text=str(options[1]),
        value="" if secret else _render(getattr(config, key)),
        default="" if secret else _render(options[0]),
        choices=choices,
        editable=not reason,
        read_only_reason=reason,
        warning=DANGEROUS.get(key, ""),
    )


def list_settings() -> list[PlatformSetting]:
    """Every Constance setting, in fieldset order."""
    groups = _groups()
    ordered = list(groups) + [key for key in settings.CONSTANCE_CONFIG if key not in groups]
    return [_setting(key, groups.get(key, "Other")) for key in ordered]


def get_setting(key: str) -> PlatformSetting:
    if key not in settings.CONSTANCE_CONFIG:
        raise PlatformSettingError("NOT_FOUND", f"no platform setting {key!r}")
    return _setting(key, _groups().get(key, "Other"))


def _editable(key: str, *, confirmed: bool) -> PlatformSetting:
    current = get_setting(key)
    if not current.editable:
        raise PlatformSettingError("VALIDATION", current.read_only_reason)
    if current.warning and not confirmed:
        raise PlatformSettingError("PRECONDITION", f"confirm the change: {current.warning}")
    return current


def set_setting(key: str, raw: str, *, confirmed: bool = False) -> tuple[PlatformSetting, PlatformSetting]:
    """Clean ``raw`` with the key's form field and store it. Returns ``(before, after)``."""
    from constance import config

    before = _editable(key, confirmed=confirmed)
    field = _field(key)
    if before.kind == "bool":
        if raw not in ("true", "false"):
            raise PlatformSettingError("VALIDATION", "a yes/no setting takes true or false")
        value: Any = raw == "true"
    else:
        try:
            value = field.clean(raw)
        except forms.ValidationError as exc:
            raise PlatformSettingError("VALIDATION", " ".join(str(m) for m in exc.messages)) from None
    setattr(config, key, value)
    return before, get_setting(key)


def reset_setting(key: str, *, confirmed: bool = False) -> tuple[PlatformSetting, PlatformSetting]:
    """Put ``key`` back to its default. Returns ``(before, after)``."""
    from constance import config

    before = _editable(key, confirmed=confirmed)
    setattr(config, key, settings.CONSTANCE_CONFIG[key][0])
    return before, get_setting(key)
