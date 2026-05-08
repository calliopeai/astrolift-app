# Docker Configuration

To run this environment:

```shell
bash run.sh
```

## Coexisting with other dev stacks

All host port bindings are localhost-only (`127.0.0.1`) and individually
overridable. If a port collides with another stack on this host, copy
`.env.example` to `.env` and uncomment the var you need to remap:

```shell
cp .env.example .env
# edit, e.g. ASTROLIFT_DJANGO_PORT=18000
docker compose up
```

Defaults (host:container):

| Service           | Var                                | Default |
| ----------------- | ---------------------------------- | ------- |
| Django API        | `ASTROLIFT_DJANGO_PORT`            | 8000    |
| Next.js UI        | `ASTROLIFT_UI_PORT`                | 3000    |
| Postgres          | `ASTROLIFT_POSTGRES_PORT`          | 5432    |
| Redis             | `ASTROLIFT_REDIS_PORT`             | 6379    |
| OpenSearch        | `ASTROLIFT_OPENSEARCH_PORT`        | 9200    |
| Temporal gRPC     | `ASTROLIFT_TEMPORAL_GRPC_PORT`     | 7233    |
| Temporal UI       | `ASTROLIFT_TEMPORAL_UI_PORT`       | 8233    |
| MinIO S3          | `ASTROLIFT_MINIO_S3_PORT`          | 9000    |
| MinIO Console     | `ASTROLIFT_MINIO_CONSOLE_PORT`     | 9001    |
| Mailpit web       | `ASTROLIFT_MAILPIT_WEB_PORT`       | 8025    |
| Mailpit SMTP      | `ASTROLIFT_MAILPIT_SMTP_PORT`      | 1025    |
| Postgres exporter | `ASTROLIFT_POSTGRES_EXPORTER_PORT` | 9187    |
| Redis exporter    | `ASTROLIFT_REDIS_EXPORTER_PORT`    | 9121    |
