# Control-plane migrations and paired releases

Apply the new backend image's migrations successfully before updating either
the web service or the workflow worker. The worker runs
`python -m astrolift_workflows` directly and does not migrate the database.
Startup of the web backend exits with the migration command's nonzero status
before bootstrap commands or the HTTP server if migration fails (#2187).
Successful startup retains the existing bootstrap commands, production server
and development reload behavior. Other startup commands keep their existing
best-effort behavior; their errors are logged.

For a release, record the current web and worker task revisions and image
digests, then prepare the new backend and frontend images from the same tested
release. The web backend and worker must use the same backend digest; update
the web task's backend and frontend together.

1. Run a one-off task or Job using the new backend image with the target
   installation's database settings, secret references, runtime role and network.
   Override its command with:

   ```sh
   python manage.py migrate --noinput && python manage.py migrate --check
   ```

   On ECS, a new worker task revision can supply those settings. Override the
   `worker` container command with `sh`, `-c`, and the command above, and use the
   service's existing network configuration. Inspect `run-task` failures, wait
   for the task to stop, and require that container's exit code to be zero.
   Kubernetes installations can run the same command in a one-off Job with
   their backend's settings. Keep credentials in the existing secret store.
2. If migration fails, leave the services on their current revisions. Inspect
   the migration task's logs and `manage.py showmigrations` / `migrate --plan`,
   resolve the cause, then retry the migration prerequisite. An HTTP health
   check alone does not prove that this step succeeded, especially for an
   older image that continued startup after migration failure.
3. After success, roll out the worker and verify that it polls its task queue
   without schema errors. Roll out the paired backend/frontend web revision,
   then verify service stability, backend readiness and authenticated
   GraphQL/UI behavior. Preserve live task settings and secret references
   when registering ECS revisions.

PostgreSQL migrations use a database-scoped advisory lock that covers planning,
execution and post-migrate hooks. Web startup's normal migration rerun is safe
alongside the one-off task when both use an image containing this command.

For an image rollback, first verify that the previous backend and worker are
compatible with the schema already applied. Restore the recorded worker and
paired web revisions together and repeat the service and application checks.
An image rollback does not reverse schema or data migrations. A schema rollback
requires its own reviewed migration and database recovery plan; do not run a
reverse migration merely to match an older image. Prefer a forward repair when
the schema cannot support that image.
