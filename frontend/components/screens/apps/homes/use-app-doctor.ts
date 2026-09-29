"use client";

import { useLazyQuery, useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  RERUN_ONBOARDING,
  RETRY_ASTROLIFT_AUTOWIRE,
  TRIGGER_DEPLOY_WORKFLOW,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { RESYNC_MANIFEST_FROM_REPO } from "@/graphql/registry/registry.mutations";
import { GET_APP_DOCTOR } from "@/graphql/registry/registry.queries";
import type { PermissionCheck } from "@/lib/permissions/astrolift-permissions";

export interface DoctorCheck {
  key: string;
  status: string;
  detail: string;
  fix: string;
}

export interface DoctorReport {
  healthy: boolean;
  checks: DoctorCheck[];
}

interface DoctorResp {
  astroliftAppDoctor: DoctorReport | null;
}

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
export const REPAIRS: Record<
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

/**
 * The data half of AppDoctorPanelView (#1550): the on-demand probe and the
 * repairs its checks point at.
 *
 * On demand, never on mount. Each run resolves hostnames, reads a push-role
 * trust policy, and runs the idempotent manifest resync, so probing on every
 * page view would be both slow and rude to the provider APIs.
 */
export function useAppDoctor(appSlug: string) {
  const [run, { data, loading, error, called }] = useLazyQuery<DoctorResp>(GET_APP_DOCTOR, {
    // The point of the panel is a fresh reading; a cached one would answer
    // the question the operator asked last time.
    fetchPolicy: "network-only",
  });

  const runChecks = React.useCallback(() => {
    void run({ variables: { appSlug } });
  }, [appSlug, run]);
  const { run: repair, running } = useRepair(appSlug, runChecks);

  return {
    report: data?.astroliftAppDoctor ?? null,
    loading,
    errorMessage: error?.message ?? null,
    called,
    runChecks,
    /** Runs the repair for a check's `fix` verb, then re-probes. */
    repair,
    /** The verb whose repair is in flight, if any. */
    running,
  };
}
