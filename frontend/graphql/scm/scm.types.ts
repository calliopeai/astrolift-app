import type { AstroliftGuid, MutationResult } from "@/graphql/identity/identity.types";

export type ScmConnectionKind =
  | "github_oauth_app"
  | "github_app_install"
  | "github_pat"
  | "gitlab_oauth_app"
  | "gitlab_pat"
  | "bitbucket_oauth_app"
  | "bitbucket_pat"
  | "gitea_oauth_app"
  | "gitea_pat";

export type ScmVisibilityScope =
  | "private_org"
  | "public_org"
  | "user_repos"
  | "public_non_org";

export interface AstroliftSourceConnection {
  id: AstroliftGuid;
  kind: ScmConnectionKind;
  name: string;
  displayName: string;
  accountLogin: string;
  installationId: string;
  apiBaseUrl: string;
  oauthClientId: string;
  oauthRedirectUri: string;
  repoVisibilityScopes: ScmVisibilityScope[];
  isOauthAppConfig: boolean;
  isActive: boolean;
  tokenExpiresAt: string | null;
  lastUsedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AstroliftSshDeployKey {
  id: AstroliftGuid;
  name: string;
  publicKey: string;
  fingerprintSha256: string;
  registeredAppSlug: string | null;
  lastUsedAt: string | null;
  isActive: boolean;
  createdAt: string;
}

export interface AstroliftSshDeployKeyCreated {
  key: AstroliftSshDeployKey;
}

export type { MutationResult };
