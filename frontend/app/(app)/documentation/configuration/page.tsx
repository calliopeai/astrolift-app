import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export const metadata = {
  title: "Configuration · Documentation · Astrolift",
};

type Source = "image" | "operator" | "platform";

interface EnvVar {
  name: string;
  purpose: string;
  default?: string;
  required: boolean;
  source: Source;
}

interface Group {
  id: string;
  title: string;
  intro: string;
  vars: EnvVar[];
}

const groups: Group[] = [
  {
    id: "core-platform",
    title: "Core platform",
    intro:
      "Process-level Django settings. Every install must set the first three; the rest have safe defaults.",
    vars: [
      {
        name: "DJANGO_SECRET_KEY",
        purpose:
          "Cryptographic key for signing cookies, CSRF tokens, password reset URLs.",
        default: "not-a-secret (dev only)",
        required: true,
        source: "operator",
      },
      {
        name: "APP_BASE_URL",
        purpose:
          "Public URL the frontend is reachable at. Used to build invitation links, the public webhook URL embedded in the GitHub App manifest, and absolute URLs in outbound email.",
        default: "(empty)",
        required: true,
        source: "operator",
      },
      {
        name: "DJANGO_CONFIGURATION",
        purpose:
          "Environment selector: Dev, Local, LocalPG, LocalVerbose, Tests, int, stg, Prd. Drives Sentry init, ALLOWED_HOSTS defaults, and password validators.",
        default: "Dev",
        required: false,
        source: "operator",
      },
      {
        name: "DJANGO_DEBUG",
        purpose:
          "Django DEBUG mode. Leave on in dev so 500s show the traceback; production installs typically keep it on too because the rendered debug page is gated on the request host.",
        default: "false",
        required: false,
        source: "operator",
      },
      {
        name: "DJANGO_BASE_URL",
        purpose:
          "URL prefix the Django app mounts under. Behind a reverse proxy that strips a prefix, leave as default; if Astrolift owns the root path set to empty.",
        default: "app/",
        required: false,
        source: "operator",
      },
      {
        name: "FRONTEND_URL",
        purpose:
          "Where the Next.js UI is hosted. Embedded in emails so the recipient lands on the UI, not the API.",
        default: "http://localhost:3000",
        required: false,
        source: "operator",
      },
      {
        name: "SERVER_NAME",
        purpose:
          "Cluster-facing hostname used in computed S3 custom-domain fallback and a few link builders.",
        default: "localhost",
        required: false,
        source: "operator",
      },
      {
        name: "DOTENV",
        purpose:
          "Path to a .env file loaded at process start. Useful for local development; production should pass env directly.",
        default: "backend/config/local.env",
        required: false,
        source: "operator",
      },
    ],
  },
  {
    id: "database",
    title: "Database",
    intro:
      "Postgres connection settings. All five values are required — the Django ORM has no fallback.",
    vars: [
      {
        name: "POSTGRES_ENGINE",
        purpose:
          "Django database backend. Set to django.db.backends.postgresql for Postgres.",
        default: "(unset)",
        required: true,
        source: "operator",
      },
      {
        name: "POSTGRES_HOST",
        purpose: "Postgres server hostname or socket path.",
        required: true,
        source: "operator",
      },
      {
        name: "POSTGRES_PORT",
        purpose: "Postgres listening port.",
        default: "5432",
        required: true,
        source: "operator",
      },
      {
        name: "POSTGRES_DB",
        purpose: "Database name Astrolift owns.",
        required: true,
        source: "operator",
      },
      {
        name: "POSTGRES_USER",
        purpose:
          "Database role. Needs CREATEDB only during initial migrations, otherwise plain owner privileges on the database are enough.",
        required: true,
        source: "operator",
      },
      {
        name: "POSTGRES_PASSWORD",
        purpose: "Password for the role.",
        required: true,
        source: "operator",
      },
      {
        name: "POSTGRES_TLS_CERT_PATH",
        purpose:
          "Path to a PEM client certificate for mTLS to Postgres. Optional — only set when the Postgres deployment requires it (e.g. internal CA).",
        required: false,
        source: "operator",
      },
    ],
  },
  {
    id: "cache-queue",
    title: "Cache & queue",
    intro:
      "Redis powers Django's cache layer and the in-cluster Redis managed service injects REDIS_URL into apps that bind one.",
    vars: [
      {
        name: "DJANGO_CACHE_URL",
        purpose:
          "Redis URL for the Django cache backend. Format: redis://host:port/db.",
        required: true,
        source: "operator",
      },
      {
        name: "REDIS_URL",
        purpose:
          "Read by app workloads (not the control plane). When an app binds the in-cluster Redis managed service, the platform injects this var into the pod env automatically.",
        required: false,
        source: "platform",
      },
    ],
  },
  {
    id: "temporal",
    title: "Temporal",
    intro:
      "The control plane runs durable workflows on a Temporal cluster. The default points at the docker-compose local Temporal.",
    vars: [
      {
        name: "TEMPORAL_ADDRESS",
        purpose: "host:port of the Temporal frontend service.",
        default: "localhost:7233",
        required: false,
        source: "operator",
      },
      {
        name: "TEMPORAL_NAMESPACE",
        purpose:
          "Temporal namespace. Astrolift defaults to 'default'; production installs typically use a dedicated namespace per environment.",
        default: "default",
        required: false,
        source: "operator",
      },
      {
        name: "TEMPORAL_TASK_QUEUE",
        purpose:
          "Task queue workers poll. The bundled worker image defaults to this same name; override on both sides if you run multiple worker fleets.",
        default: "astrolift-main",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_SKIP_TEMPORAL_TESTS",
        purpose:
          "Test-only toggle. When set, tests marked as requiring Temporal skip themselves; useful when iterating without a Temporal dev server.",
        default: "(unset)",
        required: false,
        source: "operator",
      },
    ],
  },
  {
    id: "identity-auth",
    title: "Identity & auth",
    intro:
      "Auth0 is the default OIDC client but any OIDC provider works — see the identity-providers guide. The ASTROLIFT_* bootstrap vars are read once by the management commands.",
    vars: [
      {
        name: "AUTH0_DOMAIN",
        purpose:
          "Auth0 tenant domain (or hosted-UI domain for other OIDC providers). Combined with the well-known URL to construct discovery.",
        required: true,
        source: "operator",
      },
      {
        name: "AUTH0_SERVER_METADATA_URL",
        purpose:
          "Explicit OIDC discovery URL. Required when the discovery doc lives on a different host than AUTH0_DOMAIN (notably AWS Cognito).",
        required: false,
        source: "operator",
      },
      {
        name: "AUTH0_CLIENT_ID",
        purpose: "OIDC client ID Astrolift presents to the IdP.",
        required: true,
        source: "operator",
      },
      {
        name: "AUTH0_CLIENT_SECRET",
        purpose: "OIDC client secret.",
        required: true,
        source: "operator",
      },
      {
        name: "AUTH0_CLIENT_SCOPES",
        purpose: "OIDC scopes requested at sign-in.",
        default: "openid profile email read:users create:users update:users",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_AUTO_SIGNUP_DOMAINS",
        purpose:
          "Comma-separated list of email domains allowed to auto-create a profile on first OIDC sign-in. Empty disables auto-signup.",
        default: "(empty — disabled)",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_LOCAL_LOGIN_ENABLED",
        purpose:
          "Enables the development-only local login form at /app/auth1/local/. Production installs must leave this unset — the route 404s when disabled.",
        default: "false",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_ADMIN_EMAIL",
        purpose:
          "Bootstrap admin user email. Read by the bootstrap_admin management command on first install.",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_ADMIN_PASSWORD",
        purpose:
          "Bootstrap admin user password. Only used when bootstrap_admin creates the user; ignored after.",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_ADMIN_SUPERUSER",
        purpose:
          "true to grant Django superuser on the bootstrapped admin (admin panel access).",
        default: "false",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_ORG_SLUG",
        purpose: "Bootstrap organization slug. Used by bootstrap_admin and bootstrap_idp.",
        default: "acme",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_ORG_NAME",
        purpose: "Bootstrap organization display name.",
        default: "(derived from ASTROLIFT_ORG_SLUG)",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_IDP_KIND",
        purpose:
          "Identity provider kind for the bootstrap IdP: oidc, saml, cognito, auth0, okta, azure_ad, google, github.",
        default: "oidc",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_IDP_DISPLAY_NAME",
        purpose: "Display name shown on the IdP card in Settings.",
        default: "(= ASTROLIFT_IDP_KIND)",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_IDP_CLIENT_ID",
        purpose: "Bootstrap IdP OIDC client ID (read by bootstrap_idp).",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_IDP_DISCOVERY_URL",
        purpose:
          "OIDC discovery URL for the bootstrap IdP (.well-known/openid-configuration).",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_IDP_CLIENT_SECRET_REF",
        purpose:
          "Reference (not the literal value) to the IdP client secret in the configured secrets backend.",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_IDP_IS_DEFAULT",
        purpose:
          "true to mark the bootstrap IdP active on creation (the org's sign-in routes through it immediately).",
        default: "true",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_SECRETS_BACKEND",
        purpose:
          "Secrets backend driver: local_fernet (dev), aws_secrets_manager, gcp_secret_manager, azure_key_vault.",
        default: "local_fernet",
        required: false,
        source: "operator",
      },
    ],
  },
  {
    id: "aws",
    title: "AWS",
    intro:
      "Set when the install uses S3-compatible object storage (default) or AWS-hosted services. MinIO uses the same vars — point AWS_S3_ENDPOINT_URL at the MinIO endpoint.",
    vars: [
      {
        name: "AWS_REGION",
        purpose: "Region for AWS API clients (boto3, SES). Most installs match the EKS region.",
        default: "(boto3 default chain)",
        required: false,
        source: "operator",
      },
      {
        name: "AWS_ACCESS_KEY_ID",
        purpose:
          "Static credentials for boto3. In-cluster installs should use IRSA / workload identity and leave this unset.",
        required: false,
        source: "operator",
      },
      {
        name: "AWS_SECRET_ACCESS_KEY",
        purpose: "Static credentials for boto3. See above.",
        required: false,
        source: "operator",
      },
      {
        name: "AWS_STORAGE_BUCKET_NAME",
        purpose: "S3 bucket Astrolift writes user uploads, exported reports, and static assets to.",
        required: false,
        source: "operator",
      },
      {
        name: "AWS_S3_ENDPOINT_URL",
        purpose:
          "Override the S3 endpoint. Empty = real AWS S3; set to http://minio:9000 to use a MinIO container.",
        default: "(empty — real AWS S3)",
        required: false,
        source: "operator",
      },
      {
        name: "AWS_S3_CUSTOM_DOMAIN",
        purpose:
          "Public hostname rendered into S3 asset URLs. Useful behind a CDN.",
        required: false,
        source: "operator",
      },
      {
        name: "USE_S3",
        purpose:
          "Master switch for S3-backed media + static storage. Set to false to fall back to local disk (dev only).",
        default: "true",
        required: false,
        source: "operator",
      },
      {
        name: "AWS_SES_REGION_NAME",
        purpose: "Region for AWS SES email delivery.",
        default: "us-west-2",
        required: false,
        source: "operator",
      },
      {
        name: "AWS_SES_REGION_ENDPOINT",
        purpose: "SES endpoint URL — only override for VPC endpoints.",
        default: "email.us-west-2.amazonaws.com",
        required: false,
        source: "operator",
      },
    ],
  },
  {
    id: "email",
    title: "Email",
    intro:
      "Astrolift sends invitations and deploy notifications via SMTP or AWS SES. Set DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend for SMTP, django_ses.SESBackend for SES (default).",
    vars: [
      {
        name: "DJANGO_EMAIL_BACKEND",
        purpose: "Django email backend dotted-path.",
        default: "django_ses.SESBackend",
        required: false,
        source: "operator",
      },
      {
        name: "EMAIL_HOST",
        purpose: "SMTP host (used when the backend is the SMTP backend).",
        default: "localhost",
        required: false,
        source: "operator",
      },
      {
        name: "EMAIL_PORT",
        purpose: "SMTP port.",
        default: "25",
        required: false,
        source: "operator",
      },
      {
        name: "FROM_EMAIL",
        purpose: "From address on all outbound mail. Must match a verified SES identity in production.",
        default: "no-reply@example.com",
        required: true,
        source: "operator",
      },
    ],
  },
  {
    id: "observability",
    title: "Observability",
    intro:
      "Tracing, error tracking, and structured logging. Defaults keep local dev quiet; production installs typically set all three.",
    vars: [
      {
        name: "OTEL_EXPORTER_OTLP_ENDPOINT",
        purpose:
          "OTLP collector endpoint (e.g. http://otel-collector:4318). Unset = spans render to stdout via the ConsoleSpanExporter.",
        required: false,
        source: "operator",
      },
      {
        name: "OTEL_SERVICE_NAME",
        purpose: "service.name resource attribute on emitted spans.",
        default: "astrolift-api",
        required: false,
        source: "operator",
      },
      {
        name: "SENTRY_DSN",
        purpose:
          "Sentry project DSN. Unset = Sentry disabled; only initialized when DJANGO_CONFIGURATION is one of {dev, stg, prd}.",
        required: false,
        source: "operator",
      },
      {
        name: "LOG_LEVEL",
        purpose: "Root log level: DEBUG, INFO, WARNING, ERROR.",
        default: "INFO",
        required: false,
        source: "operator",
      },
      {
        name: "LOG_FORMAT",
        purpose:
          "Log emitter: json (production), text (local dev), or ecs (Elastic Common Schema).",
        default: "json",
        required: false,
        source: "operator",
      },
      {
        name: "LOG_DESTINATION",
        purpose: "stdout or stderr.",
        default: "stdout",
        required: false,
        source: "operator",
      },
      {
        name: "TELEMETRY_LOGS",
        purpose:
          "Toggle structured logging entirely. Local dev sets false; production stays true.",
        default: "true",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_ENABLE_DJT",
        purpose:
          "Enable Django Debug Toolbar. Off in production because the toolbar leaks SQL, settings, and cache internals to anyone who can hit the page.",
        default: "false",
        required: false,
        source: "operator",
      },
    ],
  },
  {
    id: "cluster-runtime",
    title: "Cluster runtime",
    intro:
      "Identifiers the control plane uses when it talks to the tenant cluster or its surrounding AWS infrastructure.",
    vars: [
      {
        name: "EKS_CLUSTER_NAME",
        purpose:
          "Name of the EKS / GKE / AKS cluster the control plane treats as its primary tenant runtime. Used by the cluster registration command and surfaced on cluster detail.",
        required: false,
        source: "operator",
      },
      {
        name: "SECRETS_MANAGER_PREFIX",
        purpose:
          "Path prefix on secrets created in the configured backend. Use a unique value per install to avoid collisions in shared secrets infra.",
        default: "astrolift/",
        required: false,
        source: "operator",
      },
      {
        name: "ASTROLIFT_ENV_LABEL",
        purpose:
          "Free-form label that appears on workload labels emitted by the platform (e.g. dev, stg, prd). Picked up by Prometheus scrapes for easy filtering.",
        default: "(unset)",
        required: false,
        source: "operator",
      },
    ],
  },
  {
    id: "feature-flags",
    title: "Feature flags",
    intro: (
      "Toggle whole subsystems on or off. Each flag corresponds to a Feature enum member in backend/config/features.py; the registry filters INSTALLED_APPS and the GraphQL schema based on these values."
    ),
    vars: [
      {
        name: "FEATURE_WORKFLOWS",
        purpose: "Include the workflows Django app + Temporal worker integration.",
        default: "true",
        required: false,
        source: "operator",
      },
      {
        name: "FEATURE_TEMPORAL",
        purpose:
          "Temporal-aware code paths (durable workflow registration). Off disables the durable-workflow features without removing the Django app.",
        default: "true",
        required: false,
        source: "operator",
      },
      {
        name: "FEATURE_OPENSEARCH",
        purpose: "Drives OpenSearch-backed search features.",
        default: "true",
        required: false,
        source: "operator",
      },
      {
        name: "FEATURE_FILE_UPLOADS",
        purpose: "Allow user file uploads. Off disables the upload endpoint entirely.",
        default: "true",
        required: false,
        source: "operator",
      },
      {
        name: "OPENSEARCH_URL",
        purpose: "OpenSearch base URL.",
        default: "http://localhost:9200",
        required: false,
        source: "operator",
      },
      {
        name: "OPENSEARCH_INDEXING",
        purpose:
          "Opt-in indexer toggle. Off by default keeps local / test stacks free of NXDOMAIN noise when no OpenSearch host is reachable.",
        default: "false",
        required: false,
        source: "operator",
      },
    ],
  },
];

function sourceBadge(source: Source) {
  switch (source) {
    case "image":
      return <Badge variant="outline">image</Badge>;
    case "platform":
      return <Badge variant="secondary">platform-injected</Badge>;
    case "operator":
    default:
      return <Badge variant="outline">operator</Badge>;
  }
}

export default function ConfigurationDocPage() {
  return (
    <article className="flex max-w-4xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Configuration</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Every environment variable Astrolift reads at runtime. Source ={" "}
          <Badge variant="outline" className="mx-0.5">
            operator
          </Badge>{" "}
          means the operator sets it,{" "}
          <Badge variant="outline" className="mx-0.5">
            image
          </Badge>{" "}
          means the container image bakes a default in, and{" "}
          <Badge variant="secondary" className="mx-0.5">
            platform-injected
          </Badge>{" "}
          means Astrolift writes it into the workload at deploy time.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Conventions</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            Boolean values accept{" "}
            <code>true / false / 1 / 0 / yes / no / on / off</code>{" "}
            (case-insensitive).
          </li>
          <li>
            Vars marked <strong className="text-foreground">required</strong>{" "}
            either have no default or have a default that&apos;s only safe
            for local development (e.g. <code>not-a-secret</code>).
          </li>
          <li>
            Anything not listed here is not read by Astrolift — silently
            ignored if set.
          </li>
        </ul>
      </section>

      <nav className="flex flex-col gap-2">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
          On this page
        </h2>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {groups.map((g) => (
            <li key={g.id}>
              <a
                href={`#${g.id}`}
                className="text-foreground underline-offset-2 hover:underline"
              >
                {g.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      {groups.map((group) => (
        <section key={group.id} id={group.id} className="flex flex-col gap-3">
          <h2 className="text-lg font-medium">{group.title}</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {group.intro}
          </p>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/4">Variable</TableHead>
                  <TableHead>Purpose</TableHead>
                  <TableHead className="w-32">Default</TableHead>
                  <TableHead className="w-20">Required</TableHead>
                  <TableHead className="w-32">Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.vars.map((v) => (
                  <TableRow key={v.name}>
                    <TableCell className="align-top font-mono text-xs">
                      {v.name}
                    </TableCell>
                    <TableCell className="text-muted-foreground align-top text-xs leading-relaxed">
                      {v.purpose}
                    </TableCell>
                    <TableCell className="text-muted-foreground align-top font-mono text-xs">
                      {v.default ?? "—"}
                    </TableCell>
                    <TableCell className="align-top">
                      {v.required ? (
                        <Badge
                          className="bg-amber-500/15 text-amber-700 dark:text-amber-300"
                          variant="secondary"
                        >
                          yes
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">no</span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      {sourceBadge(v.source)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      ))}

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">How to inspect what&apos;s set</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The Django management command <code>features</code> prints the
          current feature-flag state, and{" "}
          <code>python manage.py diffsettings</code> shows every Django
          setting against its default. The control plane never echoes
          secret values — settings marked sensitive print as{" "}
          <code>***</code>.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`./run.sh manage features
./run.sh manage diffsettings`}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/get-started"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Get started
            </Link>{" "}
            — bring the stack up locally; env vars there default to the
            docker-compose service names.
          </li>
          <li>
            <Link
              href="/documentation/identity-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Identity providers
            </Link>{" "}
            — the AUTH0_* / ASTROLIFT_IDP_* vars are described in detail.
          </li>
          <li>
            <Link
              href="/documentation/cluster-prerequisites"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Cluster prerequisites
            </Link>{" "}
            — what the tenant cluster needs separately from these env
            vars.
          </li>
        </ul>
      </section>
    </article>
  );
}
