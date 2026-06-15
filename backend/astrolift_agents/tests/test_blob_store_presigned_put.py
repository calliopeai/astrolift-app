"""Tests for the BlobStoreDriver presigned PUT (write) URL.

The snapshot uploader needs a presigned PUT; the protocol previously only
exposed a GET presign. These verify the S3 driver's ``presigned_upload_url``
builds a ``put_object`` presign with the right key/content-type and binds the
bucket's required SSE so an encryption-enforcing bucket policy can't reject
the PUT. A fake S3 client is injected so no boto3 / moto is needed (the cloud
SDK signing itself is out of scope — this asserts our call shape).
"""

from __future__ import annotations

from providers._sdk.blob_store import S3BlobStoreDriver


class _FakeS3Client:
    def __init__(self):
        self.calls: list[tuple[str, dict, int]] = []

    def generate_presigned_url(self, client_method, *, Params, ExpiresIn):
        self.calls.append((client_method, Params, ExpiresIn))
        return f"https://s3.example/{Params['Key']}?sig=test"


def test_s3_presigned_upload_url_is_put_object_with_sse():
    fake = _FakeS3Client()
    driver = S3BlobStoreDriver(
        bucket="snap-bucket",
        region="us-west-2",
        prefix="astrolift",
        s3_client=fake,
    )
    url = driver.presigned_upload_url(
        "snapshots/g-1/latest.jpg",
        expires_in=600,
        content_type="image/jpeg",
    )

    assert url.startswith("https://s3.example/")
    method, params, expires = fake.calls[0]
    assert method == "put_object"
    assert params["Bucket"] == "snap-bucket"
    # Prefix is prepended to the key.
    assert params["Key"] == "astrolift/snapshots/g-1/latest.jpg"
    assert params["ContentType"] == "image/jpeg"
    # Default (no KMS) binds AES256 so an SSE-enforcing bucket accepts the PUT.
    assert params["ServerSideEncryption"] == "AES256"
    assert expires == 600


def test_s3_presigned_upload_url_binds_kms_when_configured():
    fake = _FakeS3Client()
    driver = S3BlobStoreDriver(
        bucket="snap-bucket",
        region="us-west-2",
        kms_key_id="arn:aws:kms:us-west-2:111:key/abc",
        s3_client=fake,
    )
    driver.presigned_upload_url("snapshots/g-2/latest.jpg")
    _method, params, _expires = fake.calls[0]
    assert params["ServerSideEncryption"] == "aws:kms"
    assert params["SSEKMSKeyId"] == "arn:aws:kms:us-west-2:111:key/abc"


def test_s3_presigned_upload_url_does_not_head_object():
    # PUT presign is a write target — unlike the GET presign it must NOT
    # call head_object (the object may not exist yet).
    fake = _FakeS3Client()
    driver = S3BlobStoreDriver(bucket="b", region="r", s3_client=fake)
    # _FakeS3Client has no head_object; a call would AttributeError.
    driver.presigned_upload_url("snapshots/g-3/latest.jpg")
    assert fake.calls[0][0] == "put_object"
