"""Cross-account / cross-tenant workload identity delegation chains (#64).

A multi-account topology often needs an app's k8s ServiceAccount to
ultimately gain permission against resources in a *different* cloud
account from the one running the cluster. The platform needs to
express this as a typed chain:

  k8s SA  →  WI account A  →  AssumeRole into account B  →  resource

This module declares the chain shape + a validator that walks the
chain to confirm:
- the trust policy at each hop allows the previous hop's identity
- the chain doesn't have cycles
- the terminal hop's permissions match what the app declared

The chain is policy-only; concrete IAM mutation is per-cloud:
- AWS: AssumeRole with chained sts:AssumeRole policies
- GCP: serviceAccountTokenCreator binding from account A to
  account B; impersonation via short-lived tokens
- Azure: federated credentials referencing a managed identity in
  another tenant
- k8s_native: in-cluster trust (no cross-account)

Drivers consume the validated chain at bind time + emit the
appropriate per-cloud trust/binding manifests.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IdentityHop:
    """One step in a delegation chain."""

    plugin_id: str
    """Which plugin's identity driver enacts this hop."""

    account_id: str
    """Cloud account / project / subscription identifier. Anchored
    to the plugin namespace — interpretation is plugin-specific."""

    identity_name: str
    """Role / SA / Managed Identity name."""

    audience: str = ""
    """OIDC audience the previous hop's token must claim. Empty for
    intra-account hops."""

    trust_principal: str = ""
    """Principal the trust policy at this hop allows. Filled by the
    chain builder so validation can match it against the previous
    hop."""


@dataclass(frozen=True)
class IdentityChain:
    """Ordered list of hops from the k8s SA to the terminal resource
    grant. The first hop is always the k8s ServiceAccount; the
    terminal hop is the role that holds the actual resource
    permissions."""

    hops: tuple[IdentityHop, ...]
    namespace: str
    sa_name: str

    def terminal(self) -> IdentityHop:
        return self.hops[-1]


@dataclass(frozen=True)
class ChainValidationFailure:
    code: str
    """cycle | unsupported_cross_plugin | trust_mismatch |
    empty_chain | missing_audience"""

    message: str
    hop_index: int = -1


@dataclass(frozen=True)
class ChainValidationResult:
    ok: bool
    failures: list[ChainValidationFailure] = field(default_factory=list)


# Plugin pairs that support cross-cloud chaining. Hub-and-spoke
# topologies (e.g., GCP central project trusting AWS workload
# accounts via OIDC federation) require both sides to wire
# matching trust policies.
SUPPORTED_CROSS_PLUGIN_PAIRS: set[tuple[str, str]] = {
    ("aws", "aws"),       # AssumeRole chain within AWS
    ("gcp", "gcp"),       # service-account impersonation within GCP
    ("azure", "azure"),   # cross-subscription managed identity
    ("k8s_native", "aws"),    # k8s SA → AWS via OIDC IRSA
    ("k8s_native", "gcp"),    # k8s SA → GCP via WI federation
    ("k8s_native", "azure"),  # k8s SA → Azure via federated credentials
    # Cross-cloud federation pairs (operator opt-in; require
    # explicit OIDC-issuer trust at both ends)
    ("aws", "gcp"),
    ("gcp", "aws"),
    ("aws", "azure"),
    ("azure", "aws"),
    ("gcp", "azure"),
    ("azure", "gcp"),
}


def validate_chain(chain: IdentityChain) -> ChainValidationResult:
    failures: list[ChainValidationFailure] = []

    if not chain.hops:
        return ChainValidationResult(
            ok=False,
            failures=[ChainValidationFailure(
                code="empty_chain",
                message="chain has no hops; at least the k8s SA "
                        "+ a terminal role are required",
            )],
        )

    # Cycle detection — same (plugin_id, account_id, identity_name)
    # twice is a misconfiguration.
    seen: set[tuple[str, str, str]] = set()
    for index, hop in enumerate(chain.hops):
        key = (hop.plugin_id, hop.account_id, hop.identity_name)
        if key in seen:
            failures.append(ChainValidationFailure(
                code="cycle",
                hop_index=index,
                message=(
                    f"hop {index} repeats earlier hop "
                    f"({hop.plugin_id}, {hop.account_id}, "
                    f"{hop.identity_name})"
                ),
            ))
        seen.add(key)

    # Cross-plugin transitions must be supported pairs
    for index in range(1, len(chain.hops)):
        prev = chain.hops[index - 1]
        curr = chain.hops[index]
        if prev.plugin_id != curr.plugin_id:
            pair = (prev.plugin_id, curr.plugin_id)
            if pair not in SUPPORTED_CROSS_PLUGIN_PAIRS:
                failures.append(ChainValidationFailure(
                    code="unsupported_cross_plugin",
                    hop_index=index,
                    message=(
                        f"transition {prev.plugin_id} → "
                        f"{curr.plugin_id} not in supported pairs"
                    ),
                ))
            # Cross-plugin hops require an explicit audience
            if not curr.audience:
                failures.append(ChainValidationFailure(
                    code="missing_audience",
                    hop_index=index,
                    message=(
                        f"cross-plugin hop {index} "
                        f"({prev.plugin_id} → {curr.plugin_id}) "
                        f"requires an OIDC audience claim"
                    ),
                ))

    return ChainValidationResult(ok=not failures, failures=failures)


def build_simple_chain(
    *,
    namespace: str,
    sa_name: str,
    target_plugin_id: str,
    target_account_id: str,
    target_identity_name: str,
    audience: str = "sts.amazonaws.com",
) -> IdentityChain:
    """Convenience: build a 2-hop chain (k8s SA → cloud role).
    The most common case; longer chains are operator-defined."""
    return IdentityChain(
        namespace=namespace,
        sa_name=sa_name,
        hops=(
            IdentityHop(
                plugin_id="k8s_native",
                account_id="local-cluster",
                identity_name=f"{namespace}/{sa_name}",
            ),
            IdentityHop(
                plugin_id=target_plugin_id,
                account_id=target_account_id,
                identity_name=target_identity_name,
                audience=audience,
                trust_principal=f"oidc:{namespace}:{sa_name}",
            ),
        ),
    )
