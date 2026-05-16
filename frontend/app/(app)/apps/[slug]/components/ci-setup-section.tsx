"use client";

import { useQuery } from "@apollo/client/react";
import {
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  ExternalLinkIcon,
  KeyIcon,
  TerminalIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { GET_PLATFORM_API_URL } from "@/graphql/registry/registry.queries";

/**
 * CI setup section on the consolidated Settings page (#382).
 *
 * Lists the five GitHub Actions secrets an operator needs to wire CI
 * to Astrolift, with copy-buttons on the live values, and a paste-ready
 * reference workflow YAML keyed off those exact secret names.
 *
 * The deploy-token value itself never appears here — that's a one-shot
 * reveal handled by ``DeployTokenControl`` on the tokens page. We render
 * the row as masked dots with a link out to the existing reveal flow so
 * there's exactly one place token plaintext can be observed.
 */

interface Props {
  appSlug: string;
  ecrRepoUri: string;
  ecrPushRoleArn: string;
}

interface PlatformUrlResp {
  astroliftPlatformApiUrl: string;
}

interface SecretRow {
  /** GitHub Actions secret name — what the workflow references. */
  name: string;
  /** Live value to copy. ``null`` means the value lives behind another flow. */
  value: string | null;
  /** Short description rendered as a tooltip on the row icon. */
  hint: string;
  /** When ``value`` is null, render an "open this flow" link instead of copy. */
  externalHref?: string;
  externalLabel?: string;
}

export function CiSetupSection({ appSlug, ecrRepoUri, ecrPushRoleArn }: Props) {
  const { data, loading } = useQuery<PlatformUrlResp>(GET_PLATFORM_API_URL, {
    fetchPolicy: "cache-first",
  });
  const apiUrl = data?.astroliftPlatformApiUrl ?? "";

  const rows: SecretRow[] = [
    {
      name: "ASTROLIFT_PUSH_ROLE_ARN",
      value: ecrPushRoleArn,
      hint:
        "IAM role the GitHub Actions runner assumes via OIDC to push images. Provisioned by Astrolift on cluster bootstrap.",
    },
    {
      name: "ASTROLIFT_ECR_URI",
      value: ecrRepoUri,
      hint: "Container registry URI this app pushes to. Tag with the commit SHA per build.",
    },
    {
      name: "ASTROLIFT_APP_SLUG",
      value: appSlug,
      hint: "Stable app identifier — embed in the deploy webhook payload so the platform routes correctly.",
    },
    {
      name: "ASTROLIFT_API_URL",
      value: apiUrl,
      hint: "Public base URL of the Astrolift platform API. The deploy webhook is POSTed here.",
    },
    {
      name: "ASTROLIFT_DEPLOY_TOKEN",
      value: null,
      hint: "App-scoped bearer token. Shown once at mint time on the deploy-tokens page.",
      externalHref: `/apps/${appSlug}/tokens`,
      externalLabel: "Manage deploy tokens",
    },
  ];

  const workflowYaml = renderWorkflowYaml();

  async function copyYaml() {
    try {
      await navigator.clipboard.writeText(workflowYaml);
      toast.success("Reference workflow YAML copied.");
    } catch {
      toast.error("Copy failed — select the text and copy manually.");
    }
  }

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
          <TerminalIcon className="size-5" />
        </div>
        <div>
          <h2 className="text-base font-semibold">CI setup</h2>
          <p className="text-muted-foreground mt-0.5 text-xs">
            Paste these values into your GitHub repo&rsquo;s Actions secrets, then drop in the
            reference workflow below. The workflow keys off these exact names — rename one and the
            run breaks.
          </p>
        </div>
      </div>

      <TooltipProvider>
        <div className="bg-card overflow-hidden rounded-md border">
          {rows.map((row, idx) => (
            <SecretRowItem
              key={row.name}
              row={row}
              isLast={idx === rows.length - 1}
              loading={loading && row.name === "ASTROLIFT_API_URL"}
            />
          ))}
        </div>
      </TooltipProvider>

      <details className="group bg-muted/30 mt-4 rounded-md border">
        <summary className="hover:bg-muted/50 flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium">
          <span className="flex items-center gap-2">
            <ChevronDownIcon className="size-4 transition-transform group-open:rotate-180" />
            Reference GitHub Actions workflow
          </span>
          <span className="text-muted-foreground text-[11px]">
            paste into <span className="font-mono">.github/workflows/astrolift-ci.yml</span>
          </span>
        </summary>
        <div className="border-t">
          <pre className="bg-background overflow-x-auto p-4 font-mono text-[11px] leading-relaxed">
            {workflowYaml}
          </pre>
          <div className="flex justify-end border-t p-3">
            <Button size="sm" variant="outline" onClick={copyYaml} className="gap-1.5">
              <CopyIcon className="size-3.5" />
              Copy workflow YAML
            </Button>
          </div>
        </div>
      </details>
    </section>
  );
}

function SecretRowItem({
  row,
  isLast,
  loading,
}: {
  row: SecretRow;
  isLast: boolean;
  loading: boolean;
}) {
  return (
    <div
      className={
        "flex flex-wrap items-center gap-3 px-4 py-3 sm:flex-nowrap" +
        (isLast ? "" : " border-b")
      }
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-muted-foreground shrink-0 cursor-help">
            <KeyIcon className="size-3.5" />
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">
          <span>{row.hint}</span>
          <br />
          <span className="text-background/70">GitHub Actions secret name</span>
        </TooltipContent>
      </Tooltip>

      <code className="text-foreground shrink-0 font-mono text-xs">{row.name}</code>

      <span className="text-muted-foreground mx-1 hidden text-xs sm:inline">=</span>

      <SecretValue row={row} loading={loading} />
    </div>
  );
}

function SecretValue({ row, loading }: { row: SecretRow; loading: boolean }) {
  const [copied, setCopied] = React.useState(false);

  if (row.value === null) {
    return (
      <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
        <span className="text-muted-foreground truncate font-mono text-xs tracking-widest">
          ••••••••••••
        </span>
        {row.externalHref && (
          <Button size="sm" variant="ghost" asChild className="shrink-0 gap-1.5">
            <Link href={row.externalHref}>
              <ExternalLinkIcon className="size-3.5" />
              {row.externalLabel ?? "Open"}
            </Link>
          </Button>
        )}
      </div>
    );
  }

  if (loading) {
    return <Skeleton className="h-5 flex-1" />;
  }

  const hasValue = row.value.length > 0;

  async function copy() {
    if (!hasValue) return;
    try {
      await navigator.clipboard.writeText(row.value!);
      setCopied(true);
      toast.success(`${row.name} copied.`);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Copy failed — select the value and copy manually.");
    }
  }

  return (
    <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
      {hasValue ? (
        <code
          className="text-foreground truncate font-mono text-xs"
          title={row.value}
        >
          {row.value}
        </code>
      ) : (
        <span className="text-muted-foreground text-xs italic">
          pending provisioning…
        </span>
      )}
      <Button
        size="sm"
        variant="ghost"
        onClick={copy}
        disabled={!hasValue}
        className="shrink-0 gap-1.5"
      >
        {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/**
 * Reference GitHub Actions workflow.
 *
 * Keyed off the five ``ASTROLIFT_*`` secret names rendered above so the
 * operator can paste this verbatim — no per-app substitutions. The
 * commit SHA is the image tag; the deploy webhook POST tells the
 * platform which image to roll out. Matches the contract documented
 * in spec 09 §6.
 */
function renderWorkflowYaml(): string {
  return `name: astrolift deploy

on:
  push:
    branches: [main]
  workflow_dispatch: {}

permissions:
  id-token: write
  contents: read

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: \${{ secrets.ASTROLIFT_PUSH_ROLE_ARN }}
          aws-region: us-west-2
          role-session-name: astrolift-\${{ github.run_id }}

      - name: Login to Amazon ECR
        id: ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push image
        env:
          IMAGE: \${{ secrets.ASTROLIFT_ECR_URI }}:\${{ github.sha }}
        run: |
          docker build -t "$IMAGE" .
          docker push "$IMAGE"

      - name: Notify Astrolift
        env:
          API_URL: \${{ secrets.ASTROLIFT_API_URL }}
          APP_SLUG: \${{ secrets.ASTROLIFT_APP_SLUG }}
          TOKEN: \${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}
          IMAGE: \${{ secrets.ASTROLIFT_ECR_URI }}:\${{ github.sha }}
        run: |
          curl --fail-with-body -sS -X POST "$API_URL/api/v1/deploys" \\
            -H "Authorization: Bearer $TOKEN" \\
            -H "Content-Type: application/json" \\
            -d "{\\"appSlug\\":\\"$APP_SLUG\\",\\"image\\":\\"$IMAGE\\",\\"commitSha\\":\\"\${{ github.sha }}\\"}"
`;
}
