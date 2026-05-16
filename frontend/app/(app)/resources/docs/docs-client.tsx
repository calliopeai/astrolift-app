"use client";

import {
  BookOpenIcon,
  ExternalLinkIcon,
  SearchIcon,
} from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";

// Until the `astrolift-docs` repo is synced into the platform (either
// at build time or via a CMS), the Docs surface lists the canonical
// reading paths in the public docs repo. This is intentionally a flat
// link directory — once markdown rendering ships, swap this static
// table for a search-indexed manifest.
//
// TODO(#356 follow-on): replace with a build-time pull from
// https://github.com/calliopeai/astrolift-docs (Hugo or MDX content
// served from `public/docs/` or rendered via a server route).
const DOCS_REPO_URL = "https://github.com/calliopeai/astrolift-docs";

interface DocLink {
  title: string;
  description: string;
  href: string;
  section: string;
  tags: string[];
}

const DOCS: DocLink[] = [
  {
    title: "Platform overview",
    description:
      "What Astrolift is, where it fits in the Calliope AI ecosystem, and the BYOC tenant-runtime model.",
    href: `${DOCS_REPO_URL}#overview`,
    section: "Getting started",
    tags: ["intro", "architecture"],
  },
  {
    title: "Quickstart: deploy your first app",
    description:
      "End-to-end walkthrough — register a cluster, point a manifest at it, deploy.",
    href: `${DOCS_REPO_URL}/blob/main/quickstart.md`,
    section: "Getting started",
    tags: ["tutorial", "cli", "manifest"],
  },
  {
    title: "astrolift.toml manifest",
    description:
      "Full schema for the per-app manifest — sections, fields, validation, examples.",
    href: `${DOCS_REPO_URL}/blob/main/reference/manifest.md`,
    section: "Reference",
    tags: ["manifest", "config"],
  },
  {
    title: "Provider plugins",
    description:
      "How providers (aws, gcp, azure, k8s_native) advertise capabilities and which drivers they bundle.",
    href: `${DOCS_REPO_URL}/blob/main/reference/providers.md`,
    section: "Reference",
    tags: ["providers", "drivers", "capabilities"],
  },
  {
    title: "Cluster bring-into-management",
    description:
      "Register an operator-owned Kubernetes cluster, run preflight, install platform prereqs.",
    href: `${DOCS_REPO_URL}/blob/main/clusters/bootstrap.md`,
    section: "Operations",
    tags: ["clusters", "bootstrap", "rbac"],
  },
  {
    title: "Managed services",
    description:
      "Provision Postgres, Redis, object storage and inject the right env vars into your workload.",
    href: `${DOCS_REPO_URL}/blob/main/managed-services.md`,
    section: "Operations",
    tags: ["managed-services", "postgres", "redis"],
  },
  {
    title: "Ingress and DNS",
    description:
      "Custom domains, wildcard zones, cert issuance, the IngressDriver / DnsDriver split.",
    href: `${DOCS_REPO_URL}/blob/main/ingress-dns.md`,
    section: "Operations",
    tags: ["ingress", "dns", "tls", "domains"],
  },
  {
    title: "RBAC, teams, projects",
    description:
      "The Org → Team → Project → App scope tree and how role bindings cascade through it.",
    href: `${DOCS_REPO_URL}/blob/main/identity/rbac.md`,
    section: "Administration",
    tags: ["rbac", "teams", "permissions"],
  },
  {
    title: "API tokens",
    description: "Mint, scope, and revoke programmatic tokens for the platform API.",
    href: `${DOCS_REPO_URL}/blob/main/identity/api-tokens.md`,
    section: "Administration",
    tags: ["tokens", "api", "auth"],
  },
  {
    title: "Audit log",
    description:
      "Every mutation is recorded with actor + variables; how to subscribe via webhook for SIEM forwarding.",
    href: `${DOCS_REPO_URL}/blob/main/observability/audit.md`,
    section: "Observability",
    tags: ["audit", "compliance", "webhooks"],
  },
  {
    title: "Cost and quotas",
    description:
      "Live pricing-API-driven cost estimation, org quotas, and overage handling.",
    href: `${DOCS_REPO_URL}/blob/main/cost.md`,
    section: "Operations",
    tags: ["cost", "billing", "quotas"],
  },
  {
    title: "CLI reference",
    description: "Every astro subcommand, flag, and exit code.",
    href: `${DOCS_REPO_URL}/blob/main/cli/reference.md`,
    section: "Reference",
    tags: ["cli", "astro"],
  },
];

function groupBySection(items: DocLink[]): Record<string, DocLink[]> {
  return items.reduce<Record<string, DocLink[]>>((acc, item) => {
    if (!acc[item.section]) acc[item.section] = [];
    acc[item.section].push(item);
    return acc;
  }, {});
}

export function DocsClient() {
  const [query, setQuery] = React.useState("");

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return DOCS;
    // Simple substring match across title, description, section, tags.
    // fuse.js would be overkill for ~12 entries; once the corpus comes
    // from astrolift-docs we'll want a real search index.
    return DOCS.filter((d) => {
      const haystack = [
        d.title,
        d.description,
        d.section,
        d.tags.join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [query]);

  const grouped = React.useMemo(() => groupBySection(filtered), [filtered]);
  const sectionOrder = React.useMemo(
    () => Array.from(new Set(DOCS.map((d) => d.section))),
    [],
  );

  return (
    <PageShell
      title="Docs"
      description="Platform documentation. Markdown rendering in-app is on the roadmap — these links point at the canonical source in the astrolift-docs GitHub repo."
      actions={
        <a
          href={DOCS_REPO_URL}
          target="_blank"
          rel="noreferrer"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
        >
          <ExternalLinkIcon className="size-3.5" />
          astrolift-docs on GitHub
        </a>
      }
    >
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Coming soon: in-app docs</CardTitle>
          <CardDescription>
            We&apos;re wiring markdown content from{" "}
            <a
              href={DOCS_REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              astrolift-docs
            </a>{" "}
            into this surface. Until then, follow a link below to read on
            GitHub.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="relative max-w-md">
            <SearchIcon className="text-muted-foreground absolute left-2 top-1/2 size-4 -translate-y-1/2" />
            <Input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search docs — title, description, tag…"
              className="pl-8"
              aria-label="Search documentation"
            />
          </div>
          <p className="text-muted-foreground mt-2 text-xs">
            {filtered.length === DOCS.length
              ? `${DOCS.length} entries`
              : `${filtered.length} of ${DOCS.length} entries`}
          </p>
        </CardContent>
      </Card>

      {filtered.length === 0 ? (
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<BookOpenIcon className="size-5" />}
              title="No matches"
              description={`Nothing matched "${query}". Try a broader term or browse the full list on GitHub.`}
              learnMoreHref={DOCS_REPO_URL}
              learnMoreLabel="Open astrolift-docs"
            />
          </CardContent>
        </Card>
      ) : (
        sectionOrder
          .filter((section) => grouped[section]?.length)
          .map((section) => (
            <Card key={section}>
              <CardHeader>
                <CardTitle className="text-base">{section}</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="divide-border divide-y">
                  {grouped[section].map((doc) => (
                    <li key={doc.href} className="py-3 first:pt-0 last:pb-0">
                      <a
                        href={doc.href}
                        target="_blank"
                        rel="noreferrer"
                        className="group flex flex-col gap-1"
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium group-hover:underline">
                            {doc.title}
                          </span>
                          <ExternalLinkIcon className="text-muted-foreground size-3" />
                        </div>
                        <p className="text-muted-foreground text-xs">
                          {doc.description}
                        </p>
                        <div className="flex flex-wrap gap-1 pt-1">
                          {doc.tags.map((tag) => (
                            <Badge
                              key={tag}
                              variant="outline"
                              className="text-[10px]"
                            >
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      </a>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ))
      )}
    </PageShell>
  );
}
