"""The Storybook workbench must preserve the entire native command vocabulary."""

import json
from pathlib import Path

from nvke.operator_contract import CATALOG, REVISION


def test_storybook_contract_matches_every_native_command_option_and_choice():
    fixture = Path(__file__).resolve().parents[4] / "frontend/components/screens/nvke/operator-contract.fixture.json"
    actual = json.loads(fixture.read_text())
    assert actual == {
        "revision": REVISION,
        "commands": [
            {
                "key": key,
                "positionals": value["positionals"],
                "infrastructureChange": key in {"bootstrap.site", "management.install", "management.configure-access"},
                "options": [
                    {
                        "name": name,
                        "kind": kind["type"] if isinstance(kind, dict) else kind,
                        "choices": kind.get("choices", []) if isinstance(kind, dict) else [],
                        "required": name in value["required"],
                    }
                    for name, kind in value["options"].items()
                ],
            }
            for key, value in CATALOG.items()
        ],
    }
