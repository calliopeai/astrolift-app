"""AWS SecretsBackend implementation: Secrets Manager + SSM (#32).

Spec ref: spec 23-provider-plugin-aws + _sdk/secrets.py.

Two-tier strategy:
- Secrets Manager for material that needs rotation, KMS encryption,
  and resource policies. Default for app secrets.
- SSM Parameter Store (SecureString) for high-volume scalar config
  that doesn't need rotation. Cheaper at scale.

Operator picks per-secret via path prefix:
- ``sm:/<path>`` → Secrets Manager
- ``ssm:/<path>`` → SSM Parameter Store
- bare path → Secrets Manager (default)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.secrets import SecretsBackend
from aws._errors import NotFoundError, map_client_error


@dataclass(frozen=True)
class SecretsConfig:
    region: str
    kms_key_id: str | None = None
    """Customer-managed KMS key. None = AWS-managed default key."""

    secrets_manager_prefix: str = "astrolift"
    """Prefix on Secrets Manager names so platform-managed secrets
    are easy to spot in the AWS console + filter in IAM policies."""

    ssm_prefix: str = "/astrolift"
    """SSM parameter prefix. Leading slash is required by SSM."""


def _split_backend(path: str) -> tuple[str, str]:
    """Returns (backend, normalized_path).

    backend: 'sm' or 'ssm'
    normalized_path: with the prefix stripped
    """
    if path.startswith("sm:"):
        return "sm", path[3:]
    if path.startswith("ssm:"):
        return "ssm", path[4:]
    # Default to Secrets Manager
    return "sm", path


def _is_pending_deletion_error(exc: Exception) -> bool:
    error = getattr(exc, "response", {}).get("Error", {})
    if error.get("Code") != "InvalidRequestException":
        return False
    message = str(error.get("Message") or "").lower()
    return "scheduled for deletion" in message or "marked deleted" in message


class AWSSecretsBackend(SecretsBackend):
    """Routes between Secrets Manager and SSM based on path prefix."""

    provider_id = "aws-secrets-manager"
    supports_value_reveal = True
    value_reveal_limitation = None

    def __init__(
        self,
        *,
        config: SecretsConfig,
        sm_client: Any | None = None,
        ssm_client: Any | None = None,
    ) -> None:
        self._config = config
        if sm_client is not None:
            self._sm = sm_client
        else:
            import boto3

            self._sm = boto3.client(
                "secretsmanager",
                region_name=config.region,
            )
        if ssm_client is not None:
            self._ssm = ssm_client
        else:
            import boto3

            self._ssm = boto3.client("ssm", region_name=config.region)

    @driver_op(cloud="aws", driver="secrets")
    def ensure_initialized(self) -> dict | None:
        raise NotImplementedError("AWSSecretsBackend does not require out-of-band initialisation")

    @driver_op(cloud="aws", driver="secrets", audit=True, sensitive_kind="secret.read")
    def get(self, path: str) -> dict[str, str] | None:
        base_path, separator, field = path.partition("#")
        backend, sub = _split_backend(base_path)
        result = self._sm_get(sub) if backend == "sm" else self._ssm_get(sub)
        if not separator or result is None:
            return result
        if field not in result:
            return None
        return {field: result[field]}

    @driver_op(cloud="aws", driver="secrets", audit=True, sensitive_kind="secret.write", redact_args=("kvs",))
    def upsert(self, path: str, kvs: dict[str, str]) -> None:
        backend, sub = _split_backend(path)
        if backend == "sm":
            self._sm_upsert(sub, kvs)
        else:
            self._ssm_upsert(sub, kvs)

    @driver_op(cloud="aws", driver="secrets", audit=True, sensitive_kind="secret.delete")
    def delete(self, path: str) -> None:
        backend, sub = _split_backend(path)
        if backend == "sm":
            self._sm_delete(sub)
        else:
            self._ssm_delete(sub)

    @driver_op(cloud="aws", driver="secrets", audit=True, sensitive_kind="secret.list")
    def list(self, prefix: str) -> list[str]:
        backend, sub = _split_backend(prefix)
        if backend == "sm":
            return self._sm_list(sub)
        return self._ssm_list(sub)

    # ---- Secrets Manager backend ----------------------------------

    def _sm_name(self, path: str) -> str:
        # Resolve to a Secrets Manager name. Relative refs (app secret
        # bundles) get the backend prefix prepended; absolute refs that
        # already carry the prefix (e.g. a managed-service driver stored a
        # secret at "astrolift/rds/<inst>/url" and the binding references it
        # verbatim) are used as-is — otherwise we'd double-prefix to
        # "astrolift/astrolift/rds/..." and the lookup 404s.
        prefix = self._config.secrets_manager_prefix
        p = path.lstrip("/")
        if p.startswith("arn:"):
            return p
        if p == prefix or p.startswith(f"{prefix}/"):
            return p
        return f"{prefix}/{p}"

    def _sm_get(self, path: str) -> dict[str, str] | None:
        try:
            response = self._sm.get_secret_value(
                SecretId=self._sm_name(path),
            )
        except self._sm.exceptions.ResourceNotFoundException:
            return None
        except Exception as exc:
            if _is_pending_deletion_error(exc):
                return None
            raise map_client_error(exc) from exc

        secret_string = response.get("SecretString", "")
        if not secret_string:
            return {}
        try:
            parsed = json.loads(secret_string)
            if isinstance(parsed, dict):
                # Coerce all values to str so SecretsBackend's
                # protocol contract holds
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass
        # Plain-string secret — wrap under the conventional 'value' key
        return {"value": secret_string}

    def _sm_upsert(self, path: str, kvs: dict[str, str]) -> None:
        secret_id = self._sm_name(path)
        secret_string = json.dumps(kvs)
        try:
            kwargs: dict[str, Any] = {
                "Name": secret_id,
                "SecretString": secret_string,
            }
            if self._config.kms_key_id:
                kwargs["KmsKeyId"] = self._config.kms_key_id
            self._sm.create_secret(**kwargs)
        except self._sm.exceptions.ResourceExistsException:
            # Update path. A delete uses AWS's recovery window, so a later
            # operator re-set must restore the scheduled secret before putting
            # a new version; otherwise CRUD gets wedged for seven days.
            try:
                metadata = self._sm.describe_secret(SecretId=secret_id)
                if metadata.get("DeletedDate") is not None:
                    self._sm.restore_secret(SecretId=secret_id)
                self._sm.put_secret_value(
                    SecretId=secret_id,
                    SecretString=secret_string,
                )
            except Exception as exc:
                raise map_client_error(exc) from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _sm_delete(self, path: str) -> None:
        try:
            # ForceDeleteWithoutRecovery=False keeps a 7-30 day
            # recovery window per AWS default. Spec invariant: don't
            # hard-delete platform secrets.
            self._sm.delete_secret(
                SecretId=self._sm_name(path),
                RecoveryWindowInDays=7,
            )
        except self._sm.exceptions.ResourceNotFoundException as exc:
            raise NotFoundError(f"secret {path} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _sm_list(self, prefix: str) -> list[str]:
        try:
            full_prefix = self._sm_name(prefix).rstrip("/")
            paginator = self._sm.get_paginator("list_secrets")
            out: list[str] = []
            for page in paginator.paginate(
                Filters=[{"Key": "name", "Values": [full_prefix]}],
            ):
                for secret in page.get("SecretList", []):
                    name = secret.get("Name", "")
                    # Strip the prefix to return the operator-visible
                    # path the upsert call would use
                    if name.startswith(self._config.secrets_manager_prefix + "/"):
                        out.append(
                            name[len(self._config.secrets_manager_prefix) + 1 :],
                        )
                    else:
                        out.append(name)
            return sorted(out)
        except Exception as exc:
            raise map_client_error(exc) from exc

    # ---- SSM Parameter Store backend ------------------------------

    def _ssm_name(self, path: str) -> str:
        # SSM names must start with /
        clean = path.lstrip("/")
        return f"{self._config.ssm_prefix}/{clean}"

    def _ssm_get(self, path: str) -> dict[str, str] | None:
        # SSM parameters are scalar; we encode multi-key payloads as
        # JSON the same way the SM backend does for symmetry.
        try:
            response = self._ssm.get_parameter(
                Name=self._ssm_name(path),
                WithDecryption=True,
            )
        except self._ssm.exceptions.ParameterNotFound:
            return None
        except Exception as exc:
            raise map_client_error(exc) from exc

        value = response["Parameter"]["Value"]
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass
        return {"value": value}

    def _ssm_upsert(self, path: str, kvs: dict[str, str]) -> None:
        try:
            kwargs: dict[str, Any] = {
                "Name": self._ssm_name(path),
                "Value": json.dumps(kvs),
                "Type": "SecureString",
                "Overwrite": True,
            }
            if self._config.kms_key_id:
                kwargs["KeyId"] = self._config.kms_key_id
            self._ssm.put_parameter(**kwargs)
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _ssm_delete(self, path: str) -> None:
        try:
            self._ssm.delete_parameter(Name=self._ssm_name(path))
        except self._ssm.exceptions.ParameterNotFound as exc:
            raise NotFoundError(f"parameter {path} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _ssm_list(self, prefix: str) -> list[str]:
        try:
            full_prefix = self._ssm_name(prefix).rstrip("/")
            paginator = self._ssm.get_paginator("describe_parameters")
            out: list[str] = []
            for page in paginator.paginate(
                ParameterFilters=[
                    {
                        "Key": "Name",
                        "Option": "BeginsWith",
                        "Values": [full_prefix],
                    }
                ],
            ):
                for param in page.get("Parameters", []):
                    name = param.get("Name", "")
                    if name.startswith(self._config.ssm_prefix + "/"):
                        out.append(name[len(self._config.ssm_prefix) + 1 :])
                    else:
                        out.append(name)
            return sorted(out)
        except Exception as exc:
            raise map_client_error(exc) from exc
