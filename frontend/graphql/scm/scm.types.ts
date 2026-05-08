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
  isPersonal: boolean;
  userUsername: string | null;
  parentOauthAppId: AstroliftGuid | null;
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

export interface AstroliftWebhookSecretReveal {
  connectionId: AstroliftGuid;
  plaintextSecret: string;
  webhookUrlPath: string;
}

export interface AstroliftRemoteRepo {
  fullName: string;
  name: string;
  description: string;
  defaultBranch: string;
  visibility: string;
  cloneUrlHttps: string;
  cloneUrlSsh: string;
  webUrl: string;
  isArchived: boolean;
  isFork: boolean;
  pushedAt: string | null;
}

export interface AstroliftRemoteRepoList {
  repos: AstroliftRemoteRepo[];
  errorCode: string | null;
  errorMessage: string | null;
  recoverable: boolean;
}

export type { MutationResult };
