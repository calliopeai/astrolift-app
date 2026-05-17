"""Tests for the outbound payload format adapter (#426)."""

from __future__ import annotations

from astrolift_operations.webhook_format import (
    DISCORD,
    GENERIC,
    SLACK,
    adapt_payload,
)


def test_generic_passes_envelope_through():
    envelope = {"kind": "deploy.completed", "app": "demo", "status": "success"}
    out = adapt_payload(format=GENERIC, envelope=envelope)
    assert out == envelope
    # Verifies it's a copy — mutating the result doesn't mutate input.
    out["app"] = "other"
    assert envelope["app"] == "demo"


def test_unknown_format_falls_back_to_generic():
    envelope = {"kind": "deploy.completed", "app": "demo"}
    out = adapt_payload(format="totally-bogus", envelope=envelope)
    assert out == envelope


def test_slack_emits_text_attachments_and_meta():
    envelope = {
        "kind": "deploy.completed",
        "message": "build #42 shipped",
        "app": "demo",
        "status": "success",
    }
    out = adapt_payload(format=SLACK, envelope=envelope)
    assert "text" in out
    assert "deploy.completed" in out["text"]
    assert "build #42 shipped" in out["text"]
    assert isinstance(out["attachments"], list) and len(out["attachments"]) == 1
    attachment = out["attachments"][0]
    field_titles = {f["title"] for f in attachment.get("fields", [])}
    assert "app" in field_titles
    assert "status" in field_titles
    # Meta preserves the original envelope so operators can switch
    # back to generic without losing data.
    assert out["meta"] == envelope


def test_discord_emits_content_embeds_and_meta():
    envelope = {
        "kind": "deploy.failed",
        "message": "image scan blocked promote",
        "app": "demo",
    }
    out = adapt_payload(format=DISCORD, envelope=envelope)
    assert "content" in out
    assert "deploy.failed" in out["content"]
    embeds = out["embeds"]
    assert isinstance(embeds, list) and len(embeds) == 1
    assert embeds[0]["title"] == "deploy.failed"
    assert embeds[0]["description"] == "image scan blocked promote"
    field_names = {f["name"] for f in embeds[0].get("fields", [])}
    assert "app" in field_names
    assert out["meta"] == envelope


def test_nested_values_collapse_to_json_in_fields():
    """Nested dict / list values get serialised so they still
    appear in the channel rather than being silently dropped."""
    envelope = {
        "kind": "deploy.completed",
        "ports": [80, 443],
        "labels": {"env": "prod"},
    }
    out = adapt_payload(format=SLACK, envelope=envelope)
    fields = {f["title"]: f["value"] for f in out["attachments"][0]["fields"]}
    assert "[80, 443]" in fields["ports"] or "80" in fields["ports"]
    assert "env" in fields["labels"]


def test_envelope_with_no_extras_still_emits_summary():
    envelope = {"kind": "webhook.test"}
    slack = adapt_payload(format=SLACK, envelope=envelope)
    assert "webhook.test" in slack["text"]
    # No fields is fine — the attachment may omit them.
    assert "attachments" in slack
