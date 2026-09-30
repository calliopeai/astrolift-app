"""Shared-model audit boundary with a complete public GraphQL error envelope."""

from functools import wraps

from astrolift_graphql import failure
from core.mutations import MutationResult, mutation_audit


def model_mutation_audit(*, action, extras=None):
    """Keep audit refusal errors compatible with the complete public GraphQL envelope."""

    def decorate(fn):
        audited = mutation_audit(action=action, extras=extras)(fn)

        @wraps(audited)
        def wrapped(*args, **kwargs):
            result = audited(*args, **kwargs)
            if isinstance(result, MutationResult) and not result.ok:
                error = result.errors[0]
                message = "Model operation is unavailable." if error.code == "INTERNAL" else error.message
                return failure(str(error.code), message, field=error.field)
            return result

        return wrapped

    return decorate
