"""Build a single admitted connection with bounded, ephemeral TLS material."""

import os
import ssl
import tempfile
from contextlib import contextmanager
from pathlib import Path

from nvke.client import Endpoint, NvkeClient
from nvke.datastore import NvkeDatastore
from nvke.infrastructure_client import InfrastructureClient

from astrolift_clusters.nvke_sites import NvkeSiteError, credentials_for


@contextmanager
def connection_tls(credentials):
    try:
        tls = ssl.create_default_context(cadata=credentials.get("ca_pem") or None)
    except (ValueError, ssl.SSLError):
        raise NvkeSiteError("CREDENTIALS_UNAVAILABLE", "NVKE certificate authority is invalid") from None
    with tempfile.TemporaryDirectory(prefix="astrolift-nvke-tls-") as directory:
        if credentials.get("client_certificate_pem"):
            paths = []
            for name in ("client_certificate_pem", "client_key_pem"):
                path = Path(directory) / name
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                    stream.write(credentials[name])
                paths.append(str(path))
            try:
                tls.load_cert_chain(*paths)
            except (ValueError, ssl.SSLError, OSError):
                raise NvkeSiteError(
                    "CREDENTIALS_UNAVAILABLE", "NVKE client certificate or key is invalid"
                ) from None
        yield tls


@contextmanager
def open_connection(connection, *, namespace, checkpoint):
    checkpoint()
    credentials = credentials_for(connection)
    with connection_tls(credentials) as tls:
        endpoint = Endpoint(
            connection.surface,
            connection.endpoint,
            allow_http=connection.allow_http,
            token=(lambda: credentials_for(connection)["token"]) if credentials.get("token") else None,
        )
        options = {"verify": tls, "timeout": connection.timeout_seconds, "before_request": checkpoint}
        client = (
            NvkeDatastore(endpoint, organization_namespace=namespace, **options)
            if connection.surface == "datastore"
            else NvkeClient([endpoint], **options)
        )
        with client:
            yield client


@contextmanager
def open_infrastructure(connection, *, checkpoint):
    checkpoint()
    if connection.surface != "datastore":
        raise NvkeSiteError("SURFACE_UNAVAILABLE", "Use explicit management-Kubernetes authority")
    credentials = credentials_for(connection)
    with connection_tls(credentials) as tls:
        endpoint = Endpoint(
            "datastore",
            connection.endpoint,
            allow_http=connection.allow_http,
            token=(lambda: credentials_for(connection)["token"]) if credentials.get("token") else None,
        )
        with InfrastructureClient(
            endpoint, verify=tls, timeout=connection.timeout_seconds, before_request=checkpoint
        ) as client:
            yield client
