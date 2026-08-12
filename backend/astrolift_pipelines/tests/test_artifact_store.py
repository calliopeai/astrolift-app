from __future__ import annotations

import pytest
from django.conf import settings

from astrolift_pipelines import artifact_store
from providers._sdk.blob_store import BlobStoreNotConfiguredError, S3BlobStoreDriver


class _FakeS3Client:
    def __init__(self):
        self.puts: list[dict] = []
        self.heads: list[dict] = []
        self.presigns: list[tuple[str, dict, int]] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)

    def head_object(self, **kwargs):
        self.heads.append(kwargs)

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        self.presigns.append((method, Params, ExpiresIn))
        return "https://install-artifacts.example/payload?signature=test"


def test_configured_install_bucket_is_blob_store_fallback(monkeypatch):
    client = _FakeS3Client()
    calls: list[tuple[str, dict[str, str]]] = []

    monkeypatch.setattr(settings, "AWS_STORAGE_BUCKET_NAME", "install-artifacts", raising=False)
    monkeypatch.setattr(settings, "AWS_S3_REGION_NAME", "us-west-2", raising=False)
    monkeypatch.setattr(settings, "AWS_S3_ENDPOINT_URL", "", raising=False)
    monkeypatch.delenv("PIPELINE_ARTIFACT_LOCAL_PATH", raising=False)

    def _client(service: str, **kwargs: str):
        calls.append((service, kwargs))
        return client

    monkeypatch.setattr("boto3.client", _client)

    driver = artifact_store._get_blob_driver(object())

    assert isinstance(driver, S3BlobStoreDriver)
    assert driver._bucket == "install-artifacts"
    assert driver._s3 is client
    assert calls == [("s3", {"region_name": "us-west-2"})]

    driver.upload("acme/payloads/hash/bundle.zip", b"payload", content_type="application/zip")
    url = driver.presigned_url("acme/payloads/hash/bundle.zip", expires_in=1200)

    assert client.puts == [
        {
            "Bucket": "install-artifacts",
            "Key": "acme/payloads/hash/bundle.zip",
            "ContentType": "application/zip",
            "ServerSideEncryption": "AES256",
            "Body": b"payload",
        }
    ]
    assert client.heads == [{"Bucket": "install-artifacts", "Key": "acme/payloads/hash/bundle.zip"}]
    assert client.presigns == [
        (
            "get_object",
            {"Bucket": "install-artifacts", "Key": "acme/payloads/hash/bundle.zip"},
            1200,
        )
    ]
    assert url.startswith("https://install-artifacts.example/")


def test_configured_install_bucket_passes_s3_compatible_endpoint(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    monkeypatch.setattr(settings, "AWS_STORAGE_BUCKET_NAME", "local-artifacts", raising=False)
    monkeypatch.setattr(settings, "AWS_S3_REGION_NAME", "", raising=False)
    monkeypatch.setattr(settings, "AWS_S3_ENDPOINT_URL", "http://minio:9000", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-2")

    def _client(service: str, **kwargs: str):
        calls.append((service, kwargs))
        return _FakeS3Client()

    monkeypatch.setattr("boto3.client", _client)

    artifact_store._get_blob_driver(object())

    assert calls == [
        (
            "s3",
            {"region_name": "us-east-2", "endpoint_url": "http://minio:9000"},
        )
    ]


def test_missing_install_bucket_still_reports_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "AWS_STORAGE_BUCKET_NAME", "", raising=False)
    monkeypatch.delenv("PIPELINE_ARTIFACT_LOCAL_PATH", raising=False)

    with pytest.raises(BlobStoreNotConfiguredError):
        artifact_store._get_blob_driver(object())
