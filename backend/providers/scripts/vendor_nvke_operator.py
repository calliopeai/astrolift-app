"""Copy/check the exact shared operator contract from its immutable Opscode commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

REVISION = "4cf7e8b1ade1427bc42b1aba8d366e88b0c16131"
SOURCE = "kubernetes/nvke/operator_contract.py"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opscode", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    source = subprocess.check_output(["git", "show", f"{REVISION}:{SOURCE}"], cwd=options.opscode)
    provenance = {
        "format": 1,
        "repository": "calliopeai/astrolift-opscode",
        "revision": REVISION,
        "path": SOURCE,
        "sha256": hashlib.sha256(source).hexdigest(),
    }
    root = Path(__file__).resolve().parents[1] / "nvke"
    if options.check:
        if (root / "operator_contract.py").read_bytes() != source or json.loads(
            (root / "operator_provenance.json").read_bytes()
        ) != provenance:
            raise SystemExit("Operator contract provenance differs from the pinned source")
    else:
        (root / "operator_contract.py").write_bytes(source)
        (root / "operator_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print("Operator contract provenance verified")


if __name__ == "__main__":
    main()
