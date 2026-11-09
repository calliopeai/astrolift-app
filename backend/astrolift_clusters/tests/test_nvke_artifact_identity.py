from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.db import OperationalError, connections, transaction

from astrolift_clusters import nvke_artifact_identity as identities

pytestmark = pytest.mark.django_db(transaction=True)


def attempt(identity):
    connections.close_all()
    try:
        with transaction.atomic():
            with connections["default"].cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '100ms'")
            identities.lock(identity)
        return "acquired"
    except OperationalError as error:
        cause = error.__cause__
        assert getattr(cause, "sqlstate", getattr(cause, "pgcode", None)) == "55P03"
        return "blocked"
    finally:
        connections.close_all()


@pytest.mark.parametrize("rollback", [False, True])
def test_separate_sessions_serialize_one_uuid_and_release_on_commit_or_rollback(rollback):
    selected, other = uuid4(), uuid4()
    with ThreadPoolExecutor(max_workers=1) as worker:
        with transaction.atomic():
            identities.lock(selected)
            assert worker.submit(attempt, str(selected).upper()).result(timeout=5) == "blocked"
            assert worker.submit(attempt, other).result(timeout=5) == "acquired"
            if rollback:
                transaction.set_rollback(True)
        assert worker.submit(attempt, selected).result(timeout=5) == "acquired"


def test_reservation_requires_a_transaction():
    with pytest.raises(RuntimeError, match="PostgreSQL transaction"):
        identities.lock(uuid4())
