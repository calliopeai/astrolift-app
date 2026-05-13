"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { HammerIcon, Loader2Icon, PauseIcon, PlayIcon, RocketIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/Can";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PAUSE_ENVIRONMENT,
  RESUME_ENVIRONMENT,
  START_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface StartResp {
  startDeployment: MutationResult<AstroliftDeployment>;
}

interface PauseResp {
  pauseEnvironment: MutationResult<AstroliftAppEnvironment>;
}

interface ResumeResp {
  resumeEnvironment: MutationResult<AstroliftAppEnvironment>;
}

interface Props {
  appSlug: string;
  deployBranch: string;
}

/**
 * Live operational controls. Pause/resume is per-environment so the
 * operator can freeze prod during an incident without losing the ability
 * to ship to staging. Manual deploy reuses the latest known image tag for
 * the chosen env — when none exists, the trigger label still says "Deploy"
 * and the backend will reject with PRECONDITION_FAILED.
 */
export function ControlsSection({ appSlug, deployBranch }: Props) {
  const { data, loading } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-4">
        <h2 className="text-base font-semibold">Controls</h2>
        <p className="text-muted-foreground mt-0.5 text-xs">
          Live operational toggles. Take effect immediately — no CI roundtrip.
        </p>
      </div>

      {loading && envs.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">
          No environments yet — sync the manifest to populate this section.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {envs.map((env) => (
            <EnvironmentRow key={env.id} env={env} appSlug={appSlug} deployBranch={deployBranch} />
          ))}
        </div>
      )}
    </section>
  );
}

function EnvironmentRow({
  env,
  appSlug,
  deployBranch,
}: {
  env: AstroliftAppEnvironment;
  appSlug: string;
  deployBranch: string;
}) {
  const [pause, { loading: pausing }] = useMutation<PauseResp>(PAUSE_ENVIRONMENT, {
    refetchQueries: [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }],
    awaitRefetchQueries: true,
  });
  const [resume, { loading: resuming }] = useMutation<ResumeResp>(RESUME_ENVIRONMENT, {
    refetchQueries: [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }],
    awaitRefetchQueries: true,
  });
  const [deploy, { loading: deploying }] = useMutation<StartResp>(START_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { appSlug, limit: 10 } }],
  });
  const [imageTag, setImageTag] = useState("");

  async function handleTogglePause() {
    const fn = env.deploysPaused ? resume : pause;
    const res = await fn({ variables: { input: { id: env.id } } });
    // The mutation result envelope differs by mutation name; both share
    // `ok` and `errors[]` so we don't need to discriminate further.
    const result = env.deploysPaused
      ? (res.data as ResumeResp | null | undefined)?.resumeEnvironment
      : (res.data as PauseResp | null | undefined)?.pauseEnvironment;
    if (result?.ok) {
      toast.success(env.deploysPaused ? "Deploys resumed." : "Deploys paused.");
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Action failed.");
    }
  }

  async function handleDeploy() {
    const tag = imageTag.trim() || "latest";
    const { data } = await deploy({
      variables: {
        input: {
          appSlug,
          environmentName: env.name,
          imageTag: tag,
          triggerKind: "manual",
          branch: deployBranch || null,
        },
      },
    });
    if (data?.startDeployment.ok) {
      toast.success(`Deploy started for ${env.name} · ${tag}.`);
      setImageTag("");
    } else {
      toast.error(data?.startDeployment.errors?.[0]?.message ?? "Deploy failed.");
    }
  }

  return (
    <div className="bg-card flex flex-col gap-3 rounded-md border p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium capitalize">{env.name}</span>
          {env.deploysPaused ? (
            <Badge
              variant="outline"
              className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
            >
              <PauseIcon className="size-3" />
              Paused
            </Badge>
          ) : (
            <Badge variant="outline" className="text-muted-foreground">
              <PlayIcon className="size-3" />
              Active
            </Badge>
          )}
        </div>
        <Can permission="app.deploy">
          <Button
            size="sm"
            variant={env.deploysPaused ? "default" : "outline"}
            onClick={handleTogglePause}
            disabled={pausing || resuming}
          >
            {pausing || resuming ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : env.deploysPaused ? (
              <PlayIcon className="size-3.5" />
            ) : (
              <PauseIcon className="size-3.5" />
            )}
            {env.deploysPaused ? "Resume" : "Pause"}
          </Button>
        </Can>
      </div>

      <Can permission="app.deploy">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={imageTag}
            onChange={(e) => setImageTag(e.target.value)}
            placeholder="image tag (latest)"
            className="border-input bg-background flex-1 rounded-md border px-2 py-1 font-mono text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={handleDeploy}
            disabled={deploying || env.deploysPaused}
            title={
              env.deploysPaused
                ? "Resume deploys first."
                : "Trigger a manual deploy with the given image tag."
            }
            className="gap-1.5"
          >
            {deploying ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RocketIcon className="size-3.5" />
            )}
            Deploy
          </Button>
        </div>
      </Can>

      {env.requiredApprovals > 0 && (
        <p className="text-muted-foreground flex items-center gap-1.5 text-[11px]">
          <HammerIcon className="size-3" />
          Requires {env.requiredApprovals} approval{env.requiredApprovals === 1 ? "" : "s"}
          before rollout
        </p>
      )}
    </div>
  );
}
