"""Shared private artifact storage using the control plane's existing database."""

import io

from django.core.files.base import File
from django.core.files.storage import Storage


class BuilderArtifactStorage(Storage):
    def _query(self, name):
        from .models import BuilderArtifact

        parts = name.split("/")
        if len(parts) != 4 or parts[0] != "builder" or not parts[1].isdigit():
            raise ValueError("invalid artifact storage key")
        return BuilderArtifact.objects.filter(
            key=name, organization_id=int(parts[1]), dev_environment__guid=parts[2]
        )

    def exists(self, name):
        return self._query(name).exists()

    def get_available_name(self, name, max_length=None):
        return name

    def _open(self, name, mode="rb"):
        if mode != "rb":
            raise ValueError("artifacts are immutable")
        value = self._query(name).values_list("content", flat=True).first()
        if value is None:
            raise FileNotFoundError(name)
        return File(io.BytesIO(bytes(value)), name=name)

    def _save(self, name, content):
        from .models import BuilderArtifact, DevEnvironment

        parts = name.split("/")
        self._query(name)
        dev = DevEnvironment.objects.get(guid=parts[2], organization_id=int(parts[1]))
        raw = content.read()
        artifact, created = BuilderArtifact.objects.get_or_create(
            key=name,
            organization_id=dev.organization_id,
            dev_environment=dev,
            defaults={"content": raw, "created_by_id": dev.creator_id},
        )
        if not created and bytes(artifact.content) != raw:
            raise ValueError("immutable artifact key collision")
        return name
