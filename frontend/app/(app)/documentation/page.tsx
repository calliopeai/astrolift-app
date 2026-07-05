import Link from "next/link";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BookOpenIcon,
  CoinsIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileTextIcon,
  GitBranchIcon,
  GlobeIcon,
  GraduationCapIcon,
  KeyRoundIcon,
  ListTreeIcon,
  RocketIcon,
  ScaleIcon,
  ScrollIcon,
  ServerCogIcon,
  SettingsIcon,
  TerminalIcon,
  UsersIcon,
  WebhookIcon,
  ZapIcon,
} from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

// Canonical source repo for reference docs not yet rendered in-app.
const DOCS_REPO_URL = "https://github.com/calliopeai/astrolift-docs";

interface DocCard {
  href: string;
  title: string;
  description: string;
  icon: typeof BookOpenIcon;
  /** Opens in a new tab with an external indicator (GitHub reference). */
  external?: boolean;
}

const sections: { label: string; cards: DocCard[] }[] = [
  {
    label: "Getting started",
    cards: [
      {
        href: "/documentation/introduction",
        title: "Introduction",
        description: "What Astrolift is and the concepts you'll meet first.",
        icon: BookOpenIcon,
      },
      {
        href: "/documentation/quickstart",
        title: "Quickstart",
        description:
          "Zero to a live URL in under 10 minutes — cluster, source, deploy.",
        icon: ZapIcon,
      },
      {
        href: "/documentation/get-started",
        title: "Local dev setup",
        description: "Bring up the full stack locally with Docker Compose.",
        icon: RocketIcon,
      },
      {
        href: "/documentation/tutorials",
        title: "Tutorials",
        description:
          "Step-by-step walkthroughs for the most common build paths.",
        icon: GraduationCapIcon,
      },
    ],
  },
  {
    label: "Operator guides",
    cards: [
      {
        href: "/documentation/cluster-prerequisites",
        title: "Cluster prerequisites",
        description:
          "What a tenant cluster needs (cert-manager, ingress, external-dns) before Astrolift can use it.",
        icon: ServerCogIcon,
      },
      {
        href: "/documentation/custom-domains",
        title: "Custom domains",
        description:
          "Bind app.acme.com to an app with auto-managed TLS via cert-manager.",
        icon: GlobeIcon,
      },
      {
        href: "/documentation/source-providers",
        title: "Source providers",
        description:
          "Connect GitHub or GitLab so a git push triggers a deploy.",
        icon: GitBranchIcon,
      },
      {
        href: "/documentation/identity-providers",
        title: "Identity providers",
        description:
          "Route sign-in through OIDC, SAML, Cognito, Auth0, Okta, Azure AD, Google, or GitHub.",
        icon: KeyRoundIcon,
      },
      {
        href: "/documentation/policies",
        title: "ABAC policies",
        description:
          "Author fine-grained allow/deny rules on top of role bindings.",
        icon: ScaleIcon,
      },
      {
        href: "/documentation/webhooks",
        title: "Webhooks",
        description:
          "Subscribe external systems to Astrolift events with HMAC-signed deliveries.",
        icon: WebhookIcon,
      },
    ],
  },
  {
    label: "Runbooks",
    cards: [
      {
        href: "/documentation/runbooks",
        title: "Operator runbooks",
        description:
          "Deploy workflow, rollback, cluster management, agent dispatch, and incident response.",
        icon: AlertTriangleIcon,
      },
    ],
  },
  {
    label: "Reference",
    cards: [
      {
        href: "/documentation/configuration",
        title: "Configuration",
        description:
          "Every environment variable Astrolift reads — purpose, defaults, and who sets them.",
        icon: SettingsIcon,
      },
      {
        href: "/documentation/webhook-events",
        title: "Webhook events",
        description:
          "Payload schema and JSON examples for every outbound webhook event.",
        icon: ListTreeIcon,
      },
      {
        href: "/documentation/changelog",
        title: "Changelog",
        description: "New features and fixes, most recent first.",
        icon: ScrollIcon,
      },
    ],
  },
  {
    // Curated references that live in the astrolift-docs repo until they're
    // rendered in-app. Migrated here from the retired /resources/docs stub
    // (#894) so nothing is lost.
    label: "Reference (on GitHub)",
    cards: [
      {
        href: `${DOCS_REPO_URL}/blob/main/reference/manifest.md`,
        title: "astrolift.toml manifest",
        description:
          "Full schema for the per-app manifest — sections, fields, validation, examples.",
        icon: FileTextIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/reference/providers.md`,
        title: "Provider plugins",
        description:
          "How providers (aws, gcp, azure, k8s_native) advertise capabilities and which drivers they bundle.",
        icon: ServerCogIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/managed-services.md`,
        title: "Managed services",
        description:
          "Provision Postgres, Redis, and object storage and inject the right env vars into your workload.",
        icon: DatabaseIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/ingress-dns.md`,
        title: "Ingress and DNS",
        description:
          "Custom domains, wildcard zones, cert issuance, and the IngressDriver / DnsDriver split.",
        icon: GlobeIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/identity/rbac.md`,
        title: "RBAC, teams, projects",
        description:
          "The Org → Team → Project → App scope tree and how role bindings cascade through it.",
        icon: UsersIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/identity/api-tokens.md`,
        title: "API tokens",
        description: "Mint, scope, and revoke programmatic tokens for the platform API.",
        icon: KeyRoundIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/observability/audit.md`,
        title: "Audit log",
        description:
          "Every mutation recorded with actor + variables; how to forward via webhook for SIEM.",
        icon: ScrollIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/cost.md`,
        title: "Cost and quotas",
        description:
          "Live pricing-API-driven cost estimation, org quotas, and overage handling.",
        icon: CoinsIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/specs/multi-cloud-topology.md`,
        title: "Multi-cloud topology",
        description:
          "Modelling workloads across AWS, GCP, Azure, and self-managed clusters — placement, enforcement, federated observability.",
        icon: ListTreeIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/tree/main/demo-apps`,
        title: "Demo sample app manifests",
        description:
          "Example astrolift.toml files for Node.js, Python FastAPI, Go, and multi-workload apps.",
        icon: RocketIcon,
        external: true,
      },
      {
        href: `${DOCS_REPO_URL}/blob/main/cli/reference.md`,
        title: "CLI reference",
        description: "Every astro subcommand, flag, and exit code.",
        icon: TerminalIcon,
        external: true,
      },
    ],
  },
];

export const metadata = {
  title: "Documentation · Astrolift",
};

export default function DocumentationIndexPage() {
  return (
    <div className="flex flex-1 flex-col gap-8 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Documentation</h1>
        <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
          How Astrolift works, how to install it, and the operator runbooks
          for the surfaces that need configuration.
        </p>
      </div>

      {sections.map((section) => (
        <section key={section.label} className="flex flex-col gap-3">
          <h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
            {section.label}
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {section.cards.map((card) => {
              const Icon = card.icon;
              const body = (
                <Card className="flex h-full flex-col transition-colors group-hover:border-foreground/20">
                  <CardHeader className="pb-2">
                    <div className="bg-muted text-foreground/80 inline-flex size-9 items-center justify-center rounded-md">
                      <Icon className="size-4" />
                    </div>
                    <CardTitle className="mt-3 text-base">{card.title}</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-1 flex-col justify-between gap-3">
                    <CardDescription>{card.description}</CardDescription>
                    <span className="text-muted-foreground group-hover:text-foreground flex items-center gap-1 text-xs transition-colors">
                      {card.external ? "Read on GitHub" : "Read guide"}
                      {card.external ? (
                        <ExternalLinkIcon className="h-3 w-3" />
                      ) : (
                        <ArrowRightIcon className="h-3 w-3" />
                      )}
                    </span>
                  </CardContent>
                </Card>
              );
              return card.external ? (
                <a
                  key={card.href}
                  href={card.href}
                  target="_blank"
                  rel="noreferrer"
                  className="group"
                >
                  {body}
                </a>
              ) : (
                <Link key={card.href} href={card.href} className="group">
                  {body}
                </Link>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
