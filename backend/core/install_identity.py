"""The install's id, the same in every process of the install (#479, #2161).

A client keys an install by ``astroliftServerInfo.installId`` and routes a
push by its ``install_id``. The web replica that answered the handshake and
the worker that sent the push must therefore report the same value, across
restarts too. ``ASTROLIFT_INSTALL_ID`` wins when an operator sets it;
otherwise the id is generated once and stored in ``core.InstallIdentity``.
"""

from __future__ import annotations

import os
import secrets


def current_install_id() -> str:
    explicit = os.environ.get("ASTROLIFT_INSTALL_ID", "").strip()
    if explicit:
        return explicit
    from core.models import InstallIdentity

    # The first caller inserts the row. A concurrent first caller elsewhere
    # loses on the primary key, and get_or_create then reads the winner's id.
    row, _created = InstallIdentity.objects.get_or_create(
        pk=1, defaults={"install_id": f"install-{secrets.token_hex(12)}"}
    )
    return row.install_id
