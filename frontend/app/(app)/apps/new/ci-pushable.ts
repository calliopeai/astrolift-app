import type { ScmConnectionKind } from "@/graphql/scm/scm.types";

// SCM connection kinds that can push a commit, so `pushCiWorkflow` can seed
// the starter CI workflow. App-install / client-secret connections use the
// app's secret rather than a user token, so they can't push until an OAuth
// `_oauth_user` sibling row exists — the server rejects them and the wizard
// suppresses the affordance. Single source of truth shared by the repo-picker
// (default toggle) and the review step (disable + why-text) (#908).
export const CI_PUSHABLE_KINDS: ReadonlySet<string> = new Set([
  "github_oauth_user",
  "github_app_install",
  "github_pat",
  "gitlab_oauth_user",
  "gitlab_pat",
]);

export function isCiPushableKind(kind: ScmConnectionKind | ""): boolean {
  return CI_PUSHABLE_KINDS.has(kind);
}
