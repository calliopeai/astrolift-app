"use client";

/**
 * AppDoctorPanel (#1550) — answers "is this app fully wired?" in one place.
 *
 * The service behind this has existed and been tested since #1550 was filed,
 * and nothing called it: the checks were written, green, and unreachable, so
 * the question an operator actually asks had no answer in the product. The
 * scattered pieces it replaces are still worth knowing about — `autowire`
 * covers the repo half (webhook, CI file, deploy secret) and this covers the
 * cloud half.
 *
 * On demand, never on mount. Each run resolves hostnames, reads a push-role
 * trust policy, and runs the idempotent manifest resync, so probing on every
 * page view would be both slow and rude to the provider APIs.
 */

import { useLazyQuery, useMutation } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CircleHelpIcon,
  Loader2Icon,
  MinusCircleIcon,
  StethoscopeIcon,
  XCircleIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { Button } from "@/components/ui/button";
import {
  RERUN_ONBOARDING,
  RETRY_ASTROLIFT_AUTOWIRE,
  TRIGGER_DEPLOY_WORKFLOW,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { RESYNC_MANIFEST_FROM_REPO } from "@/graphql/registry/registry.mutations";
import { GET_APP_DOCTOR } from "@/graphql/registry/registry.queries";
import type { PermissionCheck } from "@/lib/permissions/astrolift-permissions";

interface DoctorCheck {
  key: string;
  status: string;
  detail: string;
  fix: string;
}

interface DoctorResp {
  astroliftAppDoctor: {
    healthy: boolean;
    checks: DoctorCheck[];
  } | null;
}

/** Human labels for the service's check keys. */
// Falls back to the raw key, which is why three checks added since this map
// was written have been rendering as `cronjob_runs` and `registry_repo`.
const CHECK_LABELS: Record<string, string> = {
  manifest: "Manifest",
  autowire: "Repo wiring",
  registry_repo: "Container registry",
  push_role: "Push role",
  dns: "DNS",
  identity: "Pod identity",
  image: "Image pinning",
  deployments: "Deployments",
  cronjob_runs: "Scheduled runs",
  managed_services: "Managed services",
};

/**
 * Per-status presentation. `unknown` is deliberately distinct from `fail`:
 * the probe itself errored, so the dependency is unverified rather than
 * known-broken, and telling an operator "broken" when we mean "we could not
 * look" sends them after the wrong thing.
 */
const STATUS_PRESENTATION: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; className: string; label: string }
> = {
  pass: { icon: CheckCircle2Icon, className: "text-success-fg", label: "ok" },
  fail: { icon: XCircleIcon, className: "text-danger-fg", label: "failing" },
  warn: { icon: AlertTriangleIcon, className: "text-warning-fg", label: "warning" },
  skip: { icon: MinusCircleIcon, className: "text-muted-foreground", label: "not applicable" },
  unknown: { icon: CircleHelpIcon, className: "text-muted-foreground", label: "unverified" },
};

/**
 * The `fix` verb → the mutation that repairs it. This table is the whole
 * point of the verb being machine-readable rather than prose.
 *
 * `redeploy` maps to the ordinary CI deploy, NOT `forceAstroliftRedeploy`.
 * That distinction is load-bearing and easy to get backwards: the verb reads
 * like the recovery mutation's name, but all three checks that emit it are
 * `warn` and all three want a *normal* deploy -- an unpinned image tag wants
 * a build that produces a digest, a never-deployed app wants its first
 * deploy, and surplus in-flight rows are superseded by the next one.
 * `forceAstroliftRedeploy` cancels in-flight deploys and deletes the live
 * Deployment / Service / Ingress objects, so wiring it here would turn "your
 * tag is not digest-pinned" into dropped traffic. It stays in Settings'
 * danger zone behind a typed-slug confirm, which is where a wedged app is
 * actually recovered from.
 *
 * Nothing in this table is destructive, so none of it confirms. A repair
 * that needed a confirm would be a sign it was mapped to the wrong mutation.
 */
const REPAIRS: Record<
  string,
  {
    label: string;
    permission: PermissionCheck;
    /** Present-tense progress + success wording for the toast. */
    pending: string;
    done: string;
  }
> = {
  resync_manifest: {
    label: "Resync manifest",
    permission: "app.update",
    pending: "Pulling the manifest from the repo",
    done: "Manifest resynced from the repo",
  },
  retry_autowire: {
    label: "Retry autowire",
    permission: "app.update",
    pending: "Re-running the autowire chain",
    done: "Autowire re-run",
  },
  rerun_onboarding: {
    label: "Re-run onboarding",
    permission: { allOf: ["app.update", "app.create"] },
    pending: "Re-running provisioning",
    done: "Onboarding re-run submitted",
  },
  redeploy: {
    label: "Deploy again",
    permission: "app.deploy",
    pending: "Dispatching a deploy",
    done: "Deploy dispatched",
  },
};

/**
 * Runs one repair and reports it.
 *
 * Every one of these mutations returns the `MutationResult` envelope, so a
 * transport-level success says nothing about whether the repair happened --
 * `ok: false` with a populated `errors` array is the normal failure shape and
 * reading only the promise resolution reports every denied permission as a
 * success. `rerunAstroliftOnboarding` adds a third case on top: `ok: true`
 * with `started: false`, meaning either a run is already in flight or
 * Temporal is switched off. Both are honest non-events and neither is an
 * error, so they surface as an info toast carrying the backend's own
 * `detail` rather than a green one claiming work was done.
 */
function useRepair(appSlug: string, onDone: () => void) {
  const [resyncManifest] = useMutation(RESYNC_MANIFEST_FROM_REPO);
  const [retryAutowire] = useMutation(RETRY_ASTROLIFT_AUTOWIRE);
  const [rerunOnboarding] = useMutation(RERUN_ONBOARDING);
  const [triggerDeploy] = useMutation(TRIGGER_DEPLOY_WORKFLOW);
  const [running, setRunning] = React.useState<string | null>(null);

  const run = React.useCallback(
    async (verb: string) => {
      const repair = REPAIRS[verb];
      if (!repair) return;
      setRunning(verb);
      try {
        const result = await (verb === "resync_manifest"
          ? resyncManifest({ variables: { input: { appSlug } } })
          : verb === "retry_autowire"
            ? retryAutowire({ variables: { input: { appSlug } } })
            : verb === "rerun_onboarding"
              ? rerunOnboarding({ variables: { input: { appSlug } } })
              : triggerDeploy({ variables: { input: { appSlug } } }));

        const payload = Object.values(
          (result.data ?? {}) as Record<
            string,
            {
              ok?: boolean;
              errors?: { message: string }[];
              data?: { started?: boolean; detail?: string };
            }
          >
        )[0];

        if (!payload?.ok) {
          toast.error(payload?.errors?.[0]?.message ?? `${repair.label} failed`);
          return;
        }
        if (payload.data?.started === false) {
          toast.info(payload.data.detail ?? "Nothing to do");
          return;
        }
        toast.success(repair.done);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : `${repair.label} failed`);
      } finally {
        setRunning(null);
        // Re-probe either way. A repair that failed still moves the report
        // (a partial autowire records which step broke), and a stale panel
        // showing the pre-repair state is how an operator clicks twice.
        onDone();
      }
    },
    [appSlug, onDone, rerunOnboarding, resyncManifest, retryAutowire, triggerDeploy]
  );

  return { run, running };
}

function RepairButton({
  verb,
  running,
  onRun,
}: {
  verb: string;
  running: string | null;
  onRun: (verb: string) => void;
}) {
  const repair = REPAIRS[verb];
  // An unrecognised verb renders nothing rather than a dead button. The
  // backend's own guard keeps `fix` inside this vocabulary, so reaching here
  // means the two drifted -- and a button that cannot act is worse than no
  // button, which is the defect this panel exists to surface.
  if (!repair) return null;

  return (
    <Can permission={repair.permission}>
      <Button
        size="sm"
        variant="outline"
        className="mt-1 h-6 gap-1 px-2 text-xs"
        disabled={running !== null}
        onClick={() => onRun(verb)}
      >
        {running === verb ? <Loader2Icon className="size-3 animate-spin" /> : null}
        {repair.label}
      </Button>
    </Can>
  );
}

export function AppDoctorPanel({ appSlug }: { appSlug: string }) {
  const [run, { data, loading, error, called }] = useLazyQuery<DoctorResp>(GET_APP_DOCTOR, {
    // The point of the panel is a fresh reading; a cached one would answer
    // the question the operator asked last time.
    fetchPolicy: "network-only",
  });

  const reprobe = React.useCallback(() => {
    void run({ variables: { appSlug } });
  }, [appSlug, run]);
  const { run: repair, running } = useRepair(appSlug, reprobe);

  const report = data?.astroliftAppDoctor ?? null;

  return (
    <div className="border-border flex flex-col gap-3 rounded-md border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-medium">
            <StethoscopeIcon className="size-4" />
            App doctor
          </p>
          <p className="text-muted-foreground mt-0.5 text-xs">
            Checks each dependency this app needs and reports whether it is actually usable. Runs
            live probes, so it only runs when you ask.
          </p>
        </div>
        <Can permission="app.read">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void run({ variables: { appSlug } })}
            disabled={loading}
            className="gap-1.5"
          >
            {loading ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <StethoscopeIcon className="size-3.5" />
            )}
            {called ? "Run again" : "Run checks"}
          </Button>
        </Can>
      </div>

      {error ? <p className="text-danger-fg text-xs">{error.message}</p> : null}

      {report ? (
        <>
          <p className="text-xs font-medium">
            {report.healthy ? (
              <span className="text-success-fg">Everything this app needs checks out.</span>
            ) : (
              <span className="text-warning-fg">Some dependencies need attention — see below.</span>
            )}
          </p>
          <ul className="flex flex-col gap-2">
            {report.checks.map((check) => {
              const presentation = STATUS_PRESENTATION[check.status] ?? STATUS_PRESENTATION.unknown;
              const Icon = presentation.icon;
              return (
                <li key={check.key} className="flex items-start gap-2 text-xs">
                  <Icon className={`mt-0.5 size-3.5 shrink-0 ${presentation.className}`} />
                  <span className="min-w-0">
                    <span className="font-medium">{CHECK_LABELS[check.key] ?? check.key}</span>
                    <span className="text-muted-foreground"> — {presentation.label}</span>
                    {check.detail ? (
                      <span className="text-muted-foreground block">{check.detail}</span>
                    ) : null}
                    {check.fix ? (
                      <RepairButton verb={check.fix} running={running} onRun={repair} />
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      {called && !loading && !error && report && report.checks.length === 0 ? (
        <p className="text-muted-foreground text-xs">Nothing to report for this app.</p>
      ) : null}
    </div>
  );
}
