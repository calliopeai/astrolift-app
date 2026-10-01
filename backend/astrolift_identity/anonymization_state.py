"""Existing anonymous-account state; shared by mutation and read projection."""

ANON_EMAIL_MARKER = "@anon-astrolift.net"


def is_anonymized_user(user) -> bool:
    """Matches the fixed SQL target predicate in operations migration 0028."""
    return bool(user.email and user.email.endswith(ANON_EMAIL_MARKER)) and not user.is_active
