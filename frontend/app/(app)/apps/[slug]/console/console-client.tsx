"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  TerminalIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

const SAMPLE_COMMANDS = [
  {
    label: "Open an interactive shell",
    template: (slug: string) => `astro exec --app=${slug} -- bash`,
  },
  {
    label: "Run a Django management command",
    template: (slug: string) =>
      `astro exec --app=${slug} -- python manage.py migrate --check`,
  },
  {
    label: "Tail logs from the primary workload",
    template: (slug: string) => `astro logs --app=${slug} --follow`,
  },
];

export function ConsoleClient({ slug }: { slug: string }) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp;

  if (app.loading && !a) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title="App not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${a.name} · Console`}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {a.slug} · interactive shell into the running workloads
        </span>
      }
    >
      <AppTabs slug={a.slug} active="console" />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TerminalIcon className="size-4" />
            In-browser terminal
            <Badge variant="outline" className="text-[10px] uppercase">
              coming soon
            </Badge>
          </CardTitle>
          <CardDescription>
            A web-based PTY into the primary container ships once the
            log + exec WebSocket bridge stabilizes. Use the CLI in the
            meantime — it runs the same backend protocol behind the
            scenes.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-muted/40 flex h-64 items-center justify-center rounded-md border border-dashed">
            <div className="text-muted-foreground flex flex-col items-center gap-2 text-center">
              <TerminalIcon className="size-8" />
              <p className="text-sm">In-browser terminal lands soon</p>
              <p className="max-w-md text-xs">
                Until then, the{" "}
                <code className="bg-background rounded px-1 py-0.5 font-mono">
                  astro
                </code>{" "}
                CLI gives you the same exec capability from your local
                shell.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild>
              <Link href="/downloads">
                <DownloadIcon className="size-4" />
                Install the CLI
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href={`/apps/${a.slug}/tokens`}>
                <ExternalLinkIcon className="size-4" />
                Manage deploy tokens
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">CLI shortcuts</CardTitle>
          <CardDescription>
            Common commands you&apos;d run against this app. Each one
            authenticates with your local{" "}
            <code className="bg-muted rounded px-1 py-0.5 font-mono">
              astro login
            </code>{" "}
            session.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {SAMPLE_COMMANDS.map((s) => (
            <CopyableCommand
              key={s.label}
              label={s.label}
              command={s.template(a.slug)}
            />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">What lands when</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Item
            done
            label="CLI exec via astro exec"
            note="Available today on every connected cluster."
          />
          <Item
            label="WebSocket log + exec bridge"
            note="Wires the runtime PTY into a GraphQL subscription so the in-browser terminal can attach."
          />
          <Item
            label="Web terminal"
            note="The actual xterm.js + PTY surface that renders on this page."
          />
        </CardContent>
      </Card>
    </PageShell>
  );
}

function CopyableCommand({
  label,
  command,
}: {
  label: string;
  command: string;
}) {
  return (
    <div>
      <div className="text-muted-foreground mb-1 text-xs uppercase tracking-wide">
        {label}
      </div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
          {command}
        </pre>
        <CopyButton value={command} />
      </div>
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-7 shrink-0"
      aria-label="Copy command"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          toast.success("Copied to clipboard");
          setTimeout(() => setCopied(false), 1200);
        } catch {
          toast.error("Couldn't copy — clipboard access blocked");
        }
      }}
    >
      {copied ? (
        <CheckIcon className="size-3.5" />
      ) : (
        <CopyIcon className="size-3.5" />
      )}
    </Button>
  );
}

function Item({
  done,
  label,
  note,
}: {
  done?: boolean;
  label: string;
  note: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div
        className={`mt-1 size-2 shrink-0 rounded-full ${done ? "bg-[var(--brand-primary)]" : "bg-muted-foreground/40"}`}
      />
      <div>
        <div className="text-sm font-medium">
          {label}
          {done && (
            <Badge
              variant="secondary"
              className="ml-2 bg-emerald-500/15 text-emerald-700 text-xs dark:text-emerald-300"
            >
              shipped
            </Badge>
          )}
        </div>
        <div className="text-muted-foreground text-xs">{note}</div>
      </div>
    </div>
  );
}
