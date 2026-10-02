"""Repair Moto's name-only tag lookup while retaining its native HTTP/storage path."""

from __future__ import annotations


def exact_native_tags(monkeypatch):
    from moto.events.exceptions import ResourceNotFoundException
    from moto.events.models import EventsBackend

    def resource(backend, arn):
        for bus in backend.event_buses.values():
            for candidate in (bus, *bus.rules.values()):
                if candidate.arn == arn:
                    return candidate
        raise ResourceNotFoundException("No resource matches the exact EventBridge ARN")

    def list_tags(backend, arn):
        return backend.tagger.list_tags_for_resource(resource(backend, arn).arn)

    def tag(backend, arn, tags):
        backend.tagger.tag_resource(resource(backend, arn).arn, backend.tagger.convert_dict_to_tags_input(tags))

    monkeypatch.setattr(EventsBackend, "list_tags_for_resource", list_tags)
    monkeypatch.setattr(EventsBackend, "tag_resource", tag)
