/**
 * SCM types — facade over the codegen output.
 *
 * Narrow string unions for kind + visibility scope live here so
 * switch / `?.includes` checks have valid options. The schema
 * carries them as plain `String!`.
 */

import type {
  AstroliftRemoteRepo as GeneratedRemoteRepo,
  AstroliftRemoteRepoList as GeneratedRemoteRepoList,
  AstroliftScmWebhookSecretReveal as GeneratedScmWebhookSecretReveal,
  AstroliftSourceConnection as GeneratedSourceConnection,
  AstroliftSshDeployKey as GeneratedSshDeployKey,
  AstroliftSshDeployKeyCreated as GeneratedSshDeployKeyCreated,
} from "@/graphql/__generated__/schema";
import type { AstroliftGuid, MutationResult } from "@/graphql/identity/identity.types";

export type ScmConnectionKind =
  | "github_oauth_app"
  | "github_oauth_user"
  | "github_app_install"
  | "github_pat"
  | "gitlab_oauth_app"
  | "gitlab_oauth_user"
  | "gitlab_pat"
  | "bitbucket_oauth_app"
  | "bitbucket_oauth_user"
  | "bitbucket_pat"
  | "gitea_oauth_app"
  | "gitea_oauth_user"
  | "gitea_pat";

export type ScmVisibilityScope =
  | "private_org"
  | "public_org"
  | "user_repos"
  | "public_non_org";

export type AstroliftSourceConnection = Omit<
  GeneratedSourceConnection,
  "kind" | "repoVisibilityScopes"
> & {
  kind: ScmConnectionKind;
  repoVisibilityScopes: ScmVisibilityScope[];
};

export type AstroliftSshDeployKey = GeneratedSshDeployKey;

export type AstroliftSshDeployKeyCreated = GeneratedSshDeployKeyCreated;

export type AstroliftWebhookSecretReveal = GeneratedScmWebhookSecretReveal;

export type AstroliftRemoteRepo = GeneratedRemoteRepo;

export type AstroliftRemoteRepoList = GeneratedRemoteRepoList;

export type { AstroliftGuid, MutationResult };
