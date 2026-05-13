/**
 * Canonical operator-doc URLs.
 *
 * Two destinations:
 * - `primary`: the rendered MkDocs page on astrolift.dev. Final URL
 *   shape lands when the docs site is wired up; the const below is
 *   a placeholder so a single edit cascades through every in-app
 *   reference.
 * - `wiki`: the GitHub wiki copy. Per the wiki rule (flat file
 *   structure, no subdirectories), every page lives at the wiki
 *   repo root as `Page-Name.md`.
 *
 * Both are exposed because they exist for different reasons:
 * - astrolift.dev = canonical, operator-facing, MkDocs-rendered.
 * - wiki = quick-edit, searchable inside GitHub, doesn't need a
 *   site rebuild on edit.
 *
 * The .md source in `docs/operators/` in this repo is the upstream
 * for both; a sync step (CI or manual) keeps the wiki + astrolift.dev
 * fresh.
 */

export const DOCS_BASE = "https://astrolift.dev";
export const WIKI_BASE = "https://github.com/calliopeai/astrolift-app/wiki";

interface DocLink {
  primary: string;
  wiki: string;
  label: string;
}

export const DOC_LINKS = {
  scmGithubOauth: {
    // Covers both the one-click GitHub-App manifest flow (primary path)
    // and the legacy paste-credentials options (PAT, pre-registered
    // OAuth App). Wiki page lives at the repo root per the flat-file
    // wiki convention.
    primary: `${DOCS_BASE}/scm-github-oauth/`,
    wiki: `${WIKI_BASE}/SCM-GitHub-OAuth-Setup`,
    label: "Source-provider setup guide",
  },
} as const satisfies Record<string, DocLink>;

export type DocLinkKey = keyof typeof DOC_LINKS;
