"""Explicit hosting-operator setup without changing shared ordinary-actor fixtures."""


def promote_host_operator(subject):
    subject.user.is_superuser = True
    subject.user.save(update_fields=["is_superuser"])
