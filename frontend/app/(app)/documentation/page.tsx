import Link from "next/link";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BookOpenIcon,
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
  WebhookIcon,
  ZapIcon,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const PUBLIC_DOCS_URL = "https://astrolift.dev";

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
        description: "Connect a cluster and source, register a manifest, and deploy an image.",
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
        description: "Step-by-step walkthroughs for the most common build paths.",
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
        description: "Bind app.acme.com to an app with auto-managed TLS via cert-manager.",
        icon: GlobeIcon,
      },
      {
        href: "/documentation/source-providers",
        title: "Source providers",
        description:
          "Connect GitHub or GitLab for repository access, managed CI, and source webhooks.",
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
        description: "Author fine-grained allow/deny rules on top of role bindings.",
        icon: ScaleIcon,
      },
      {
        href: "/documentation/webhooks",
        title: "Webhooks",
        description:
          "Review outbound event subscriptions, availability, and the HMAC receiver contract.",
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
        description: "Payload schema and JSON examples for every outbound webhook event.",
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
    label: "Public reference",
    cards: [
      {
        href: `${PUBLIC_DOCS_URL}/reference/astrolift-toml/`,
        title: "astrolift.toml manifest",
        description:
          "Full schema for the per-app manifest — sections, fields, validation, examples.",
        icon: FileTextIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/cli/`,
        title: "CLI reference",
        description: "Authentication, app and agent operations, CI, portable docs, and man pages.",
        icon: TerminalIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/api/`,
        title: "Control API",
        description: "GraphQL, focused REST routes, bearer authentication, tenancy, and errors.",
        icon: KeyRoundIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/mcp/`,
        title: "MCP reference",
        description:
          "Authenticated remote agent inspection, dispatch, cancellation, and source reconciliation.",
        icon: ZapIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/agent-packages/`,
        title: "Agent packages",
        description:
          "Briefs, skills, tools, source slices, monorepos, federation, and imported formats.",
        icon: ListTreeIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/workflow-toml/`,
        title: "Workflow TOML",
        description:
          "Ordered agent stages, environment recipes, skill overlays, named outputs, and repo sync.",
        icon: GitBranchIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/guides/plugin-sdk-cookbook/`,
        title: "Provider plugin SDK",
        description: "Build and test provider extensions against the current driver contracts.",
        icon: ServerCogIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/demo-scenarios/`,
        title: "Demo scenarios",
        description: "Worked app, agent, workflow, and operational validation scenarios.",
        icon: RocketIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/operators/install/`,
        title: "Install Astrolift",
        description:
          "Cloud and Kubernetes installation paths, prerequisites, and canonical runbooks.",
        icon: GlobeIcon,
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
          How Astrolift works, how to install it, and the operator runbooks for the surfaces that
          need configuration.
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
                <Card className="group-hover:border-foreground/20 flex h-full flex-col transition-colors">
                  <CardHeader className="pb-2">
                    <div className="bg-muted text-foreground/80 inline-flex size-9 items-center justify-center rounded-md">
                      <Icon className="size-4" />
                    </div>
                    <CardTitle className="mt-3 text-base">{card.title}</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-1 flex-col justify-between gap-3">
                    <CardDescription>{card.description}</CardDescription>
                    <span className="text-muted-foreground group-hover:text-foreground flex items-center gap-1 text-xs transition-colors">
                      {card.external ? "Read public docs" : "Read guide"}
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
