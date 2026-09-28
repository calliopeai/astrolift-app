import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


variables = [
    "DJANGO_SETTINGS_MODULE",
    "DJANGO_CONFIGURATION",
    "POSTGRES_ENGINE",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
]

logger.warning("[STARTUP] Env Variables v1 ==================================")

for name in variables:
    value = os.getenv(name)
    if value:
        logging.warning(f"[STARTUP] Variables {name} value found {value}")
    else:
        logging.warning(f"[STARTUP] Missing value for {name}")

logger.warning("[STARTUP] Env Variables ==================================")


def execute_and_log(command):
    logger.warning(f"[STARTUP] Running command {command}")
    # Run the command and capture the output
    result = subprocess.run(command, shell=True, text=True, capture_output=True)

    # Log the standard output and standard error
    if result.stdout:
        logging.warning("Command output: %s", result.stdout)
    if result.stderr:
        logging.error("Command error: %s", result.stderr)

    return result.returncode


# collectstatic is also baked into the Dockerfile, but running it again
# at startup is idempotent and protects against build-time skew (e.g. an
# operator deploying with a base image that pre-dates the bake step).
# bootstrap_admin + bootstrap_idp are upsert-style and no-op when their
# env vars are absent, so they're safe to run on every container start.
# bootstrap_admin must precede bootstrap_idp (IdP binds to an existing org).
ON_STARTUP = [
    "showmigrations",
    "migrate",
    "collectstatic --noinput",
    "bootstrap_admin",
    "bootstrap_idp",
    # ProviderPlugin catalog rows must exist before the Clusters page's
    # registerTenantCluster mutation can pass slug validation. The command
    # is upsert-style and reads from the in-process plugin registry that
    # AstroliftClustersConfig.ready() populated; safe to run on every boot.
    "bootstrap_provider_plugins",
    # Optional first-cluster seed. Silent no-op when ASTROLIFT_CLUSTER_SLUG
    # is unset; auto-discovers EKS values when ASTROLIFT_CLUSTER_AUTO_DISCOVER_AWS
    # is truthy. Lets operators register their first cluster at deploy time
    # rather than clicking through the UI form before the dashboard works.
    "register_tenant_cluster",
    # The install's apps zone, handed over by the installer (#374). Create-only
    # and a no-op when ASTROLIFT_MANAGED_DOMAIN_ZONE is unset.
    "bootstrap_managed_domain",
]

logger.warning("[STARTUP] Running startup... ==================================")

os.system("service cron start")

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
for command in ON_STARTUP:
    execute_and_log(f"python manage.py {command}")


# Subscriptions need an ASGI server. uvicorn handles HTTP + WS in
# one process and reloads on source change like runserver does.
# Falls back to runserver if uvicorn isn't installed (HTTP-only dev
# without subscription delivery).
#
# --reload is a dev-only convenience: the file watcher adds overhead and
# restarts the worker on any source change, which kills live websocket /
# exec / VNC streams. Mirror settings.py's dev detection (Dev + Local*)
# so production (prd/int/stg) runs uvicorn without it.
_configuration = os.getenv("DJANGO_CONFIGURATION", "Dev").lower()
_reload_flag = " --reload" if _configuration in ("dev", "local", "localpg", "localverbose") else ""
try:
    import uvicorn  # noqa: F401

    os.system(f"uvicorn config.asgi:application --host 0.0.0.0 --port 8000{_reload_flag}")
except ImportError:
    os.system("python manage.py runserver 0.0.0.0:8000")

logger.warning("Setup complete")
