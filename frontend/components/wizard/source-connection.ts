import type { SourceKind } from "@/graphql/registry/registry.types";
import type { AstroliftSourceConnection, ScmConnectionKind } from "@/graphql/scm/scm.types";

export const KIND_TO_SOURCE_KIND: Record<ScmConnectionKind, SourceKind> = {
  github_pat: "github",
  github_app_install: "github",
  github_oauth_app: "github",
  github_oauth_user: "github",
  gitlab_pat: "gitlab",
  gitlab_oauth_app: "gitlab",
  gitlab_oauth_user: "gitlab",
  bitbucket_pat: "bitbucket",
  bitbucket_oauth_app: "bitbucket",
  bitbucket_oauth_user: "bitbucket",
  gitea_pat: "gitea",
  gitea_oauth_app: "gitea",
  gitea_oauth_user: "gitea",
};

export function usableSourceConnections(connections: AstroliftSourceConnection[]) {
  return connections.filter((connection) => connection.isActive && !connection.isOauthAppConfig);
}
