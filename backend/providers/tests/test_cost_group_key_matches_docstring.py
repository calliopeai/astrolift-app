"""A billing collector's docs name the tag it actually groups by (#1470).

Both the AWS and Azure collectors described themselves as grouping on the
*binding* tag. Neither has since #1419 moved attribution onto the
managed-service id — and no resource carries a binding tag anyway, because
`ProvisionSpec.binding_id` is populated by nothing.

The failure that makes concrete: cost-allocation tags must be activated by
hand in the AWS console before Cost Explorer will group on them. An
operator debugging an empty attribution report, reading the old sentence,
would have activated `astrolift.io/binding` — a tag no resource carries —
and seen no change.

So the docstring is not decoration here; it is the instruction someone
follows. This pins it to the constant the code passes.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("module_path", "cls_name", "key_attr"),
    [
        ("aws.cost", "AWSBillingActuals", "AWS_MANAGED_SERVICE_TAG_KEY"),
        ("azure.cost", "AzureBillingActuals", "AZURE_MANAGED_SERVICE_TAG_KEY"),
    ],
)
def test_the_collector_names_the_key_it_groups_by(module_path, cls_name, key_attr):
    module = pytest.importorskip(module_path)
    doc = getattr(module, cls_name).__doc__ or ""

    assert key_attr in doc, (
        f"{cls_name} should name the constant it groups by, so the sentence "
        f"cannot drift from the call the way it did before #1470"
    )


@pytest.mark.parametrize(
    ("module_path", "cls_name"),
    [("aws.cost", "AWSBillingActuals"), ("azure.cost", "AzureBillingActuals")],
)
def test_a_binding_tag_is_not_claimed_as_the_live_group_key(module_path, cls_name):
    """The old sentence may be *described* as historical, never asserted.

    Checked on the first paragraph, which is what a reader acts on; the
    later explanation of what changed is allowed to say the word.
    """
    module = pytest.importorskip(module_path)
    doc = (getattr(module, cls_name).__doc__ or "").strip()
    first_paragraph = doc.split("\n\n", 1)[0]

    assert "binding" not in first_paragraph.lower(), (
        f"{cls_name}'s opening sentence claims a binding tag as its group key; "
        f"no resource carries one (ProvisionSpec.binding_id is never set)"
    )
