"""Private, bounded IAM transport for GUID-owned existing-model connections.

Legacy IRSA construction remains unchanged. Every native IAM read/write verifies
that this private session still resolves to its declared account and principal.
"""

from __future__ import annotations

from typing import Any

from aws.bedrock_catalogue import BedrockCatalogue, BedrockCatalogueConfig, _partition
from aws.identity_irsa import IRSAConfig, IRSADriver


class _PrivateIAM:
    def __init__(self, catalogue: BedrockCatalogue) -> None:
        from botocore.config import Config

        self.catalogue = catalogue
        catalogue._verify()
        bounds = Config(
            connect_timeout=5,
            read_timeout=20,
            retries={"total_max_attempts": 1},
            ignore_configured_endpoint_urls=True,
        )
        self.client = catalogue._session.client("iam", region_name=catalogue.config.region, config=bounds)
        self.exceptions = self.client.exceptions

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.client, name)

        def checked(**kwargs: Any) -> Any:
            self.catalogue._verify()
            try:
                return method(**kwargs)
            finally:
                self.catalogue._verify()

        return checked

    def close(self) -> None:
        try:
            self.client.close()
        finally:
            self.catalogue.close()


class NativeIRSADriver(IRSADriver):
    def __init__(self, *, config: IRSAConfig, iam_client: Any = None, session: Any = None) -> None:
        import re

        if (
            config.credential is None
            or config.credential.declared_account != config.account_id
            or not re.fullmatch(r"\d{12}", config.account_id)
            or not re.fullmatch(r"/(?:[\x21-\x7e]{1,510}/)?", config.role_path)
            or not re.fullmatch(
                r"oidc\.eks\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?/id/[A-Za-z0-9]+", config.cluster_oidc_issuer
            )
        ):
            raise ValueError("Native workload identity configuration is unavailable")
        self._partition = _partition(config.region)
        if not config.cluster_oidc_issuer.startswith(f"oidc.eks.{config.region}."):
            raise ValueError("Native workload identity configuration is unavailable")
        if iam_client is None:
            catalogue = BedrockCatalogue(BedrockCatalogueConfig(config.region, config.credential), session=session)
            try:
                iam_client = _PrivateIAM(catalogue)
            except Exception:
                catalogue.close()
                raise
        super().__init__(config=config, iam_client=iam_client)

    def _role_arn(self, role_name: str) -> str:
        path = self._config.role_path.strip("/")
        segment = f"{path}/" if path else ""
        return f"arn:{self._partition}:iam::{self._config.account_id}:role/{segment}{role_name}"

    def _oidc_trust_policy(self) -> dict[str, Any]:
        trust = super()._oidc_trust_policy()
        trust["Statement"][0]["Principal"]["Federated"] = (
            f"arn:{self._partition}:iam::{self._config.account_id}:oidc-provider/{self._config.cluster_oidc_issuer}"
        )
        return trust

    def close(self) -> None:
        close = getattr(self._iam, "close", None)
        if close:
            close()
