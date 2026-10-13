"""The public keys brain read tokens are verified against (#2320).

Unauthenticated by design: the cluster edge fetches it, and it carries public
keys only. A signing key is made here if none exists yet, so the edge already
holds it when the first token is issued.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def brain_jwks(request: HttpRequest) -> JsonResponse:
    from astrolift_registry.brain_tokens import jwks, signing_key

    signing_key()
    response = JsonResponse(jwks())
    response["Cache-Control"] = "public, max-age=300"
    return response
