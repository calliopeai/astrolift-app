import {
  ClipboardCopyIcon,
  ExternalLinkIcon,
  GithubIcon,
  LifeBuoyIcon,
  MailIcon,
  MessageSquareIcon,
} from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftOrganization } from "@/graphql/identity/identity.types";

export interface HelpScreenProps {
  platformVersion: string;
  org: Pick<AstroliftOrganization, "name" | "slug"> | null;
  copied: boolean;
  copyDiagnostics: () => void;
}

// TODO(#356 follow-on): swap these placeholders for the real per-install
// support endpoints once they're configured. Sourced today from
// hard-coded calliope.ai org references; should ultimately come from a
// `supportContacts` org-level setting on the backend.
const SUPPORT_LINKS = [
  {
    key: "discord",
    label: "Community Discord",
    description: "Real-time chat with the Astrolift team and other operators.",
    href: "https://discord.com/invite/Z9bbbE6hJv",
    icon: MessageSquareIcon,
  },
  {
    key: "github",
    label: "GitHub Issues",
    description: "File a bug, request a feature, or browse open work on astrolift-app.",
    href: "https://github.com/calliopeai/astrolift-app/issues",
    icon: GithubIcon,
  },
  {
    key: "email",
    label: "Support email",
    description: "Reach the operations team directly for account-level issues.",
    href: "mailto:support@calliope.ai",
    icon: MailIcon,
  },
] as const;

export function HelpScreen({ platformVersion, org, copied, copyDiagnostics }: HelpScreenProps) {
  return (
    <PageShell
      title="Get help"
      description="Reach the Astrolift team. When you file a ticket, include the diagnostic info below — it speeds up triage."
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <LifeBuoyIcon className="size-4" />
            Support channels
          </CardTitle>
          <CardDescription>
            Pick the channel that matches the kind of help you need.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="divide-border divide-y">
            {SUPPORT_LINKS.map((link) => {
              const Icon = link.icon;
              return (
                <li
                  key={link.key}
                  className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <div className="flex items-start gap-3">
                    <div className="bg-primary/10 text-primary rounded-md p-2">
                      <Icon className="size-4" />
                    </div>
                    <div>
                      <div className="text-sm font-medium">{link.label}</div>
                      <p className="text-muted-foreground text-xs">{link.description}</p>
                    </div>
                  </div>
                  <Button asChild size="sm" variant="outline">
                    <a href={link.href} target="_blank" rel="noreferrer">
                      <ExternalLinkIcon className="size-3.5" />
                      Open
                    </a>
                  </Button>
                </li>
              );
            })}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Diagnostic info</CardTitle>
          <CardDescription>
            Paste this into your ticket so we can match your environment.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                Platform version
              </dt>
              <dd className="mt-1 font-mono text-sm">
                <Badge variant="outline" className="font-mono">
                  {platformVersion}
                </Badge>
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                Organization
              </dt>
              <dd className="mt-1 font-mono text-sm">
                {org ? (
                  <>
                    {org.name} <span className="text-muted-foreground">({org.slug})</span>
                  </>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </dd>
            </div>
          </dl>

          <Button type="button" onClick={copyDiagnostics} variant="secondary" size="sm">
            <ClipboardCopyIcon className="size-3.5" />
            {copied ? "Copied!" : "Copy diagnostic info"}
          </Button>
          <p className="text-muted-foreground text-xs">
            Includes platform version, browser user agent, your org slug, and the current URL.
          </p>
        </CardContent>
      </Card>
    </PageShell>
  );
}
