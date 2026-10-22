/** Onboarding story fixtures: a source connection, its repos, app lifetime policies, and actions. */
import type { TemporaryAppPolicyState } from "@/components/screens/apps/temporary-app-model";
import { TEMPORARY_RULES } from "@/components/screens/apps/temporary-app.fixtures";
import type { AstroliftRemoteRepo, AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import type { RepoStepData } from "./RepoStep";
import type { OnboardingActions } from "./use-onboarding-actions";

export const CONNECTION = {
  id: "c1",
  displayName: "GitHub · conflict",
  accountLogin: "conflict",
  kind: "github_app",
  isActive: true,
} as unknown as AstroliftSourceConnection;

const repo = (name: string, over: Partial<AstroliftRemoteRepo> = {}): AstroliftRemoteRepo =>
  ({
    name,
    fullName: `conflict/${name}`,
    description: "",
    defaultBranch: "main",
    visibility: "private",
    isArchived: false,
    isFork: false,
    cloneUrlHttps: `https://github.com/conflict/${name}.git`,
    cloneUrlSsh: `git@github.com:conflict/${name}.git`,
    pushedAt: "2026-09-27T10:00:00Z",
    ...over,
  }) as AstroliftRemoteRepo;

export const REPO_DATA: RepoStepData = {
  connections: [CONNECTION],
  connectionsLoading: false,
  repos: {
    repos: [repo("checkout"), repo("billing-api"), repo("support-bot", { visibility: "public" })],
    recoverable: true,
  },
  reposLoading: false,
};

/** The organization's temporary-app policy, read: permanent apps allowed. */
export const TEMPORARY_POLICY: TemporaryAppPolicyState = {
  rules: TEMPORARY_RULES,
  loading: false,
  failed: false,
};

/** A policy that lets this member create temporary apps only. */
export const TEMPORARY_ONLY_POLICY: TemporaryAppPolicyState = {
  ...TEMPORARY_POLICY,
  rules: { ...TEMPORARY_RULES, permanentAllowed: false },
};

export const ACTIONS: OnboardingActions = {
  submit: async () => ({ ok: true }),
  skip: async () => true,
};
