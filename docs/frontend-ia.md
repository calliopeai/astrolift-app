# Astrolift Frontend — Information Architecture

> Living inventory + IA assessment of the authenticated `(app)` shell. Starting point for the navigation/IA work.
> Generated from a full nav/screen/orphan audit (2026-06-17). Counts: **119 nav links · 152 screens · 24 orphans**.
> Companion issues: `[IA]` series **#892–#919**; earlier UI bugs **#885–#891**.

## 1. How navigation is built

- **Router:** Next.js **App Router** (`app/`). Three route groups: `(app)` = authenticated shell, `(login)` = auth pages, `app/api` = route handlers.
- **Auth shell:** `app/(app)/layout.tsx` renders `AppSidebar` + `PageHeader` + `CommandPalette`; redirects to `/auth/login` without a `backend_jwt`/`sessionid` cookie.

**8 navigation surfaces:**
1. **Primary left sidebar** (`AppSidebar.tsx`) = the data-driven **workspace tree** (`NavTree.tsx`, Org › Team › Project › App from `LIST_NAV_TREE`) + the static **BROCS pillar nav** (`AstroliftNav.tsx:100-362` — the canonical nav config). The pillar nav is **admin-permission-gated**: a non-admin sees only the tree + a Register-App CTA. Each item has a per-item permission gate mirroring backend `@require_permission`.
2. **Command palette** (`CommandPalette.tsx`) — Cmd-K, ~30 shortcuts; the **only** surface linking `/cost /metrics /quotas /environments /previews`.
3. **Settings subnav** (`settings/settings-subnav.tsx`).
4. **Administration subnav** (`administration/administration-subnav.tsx`).
5. **Resources subnav** (`resources/resources-subnav.tsx`).
6. **Documentation left rail** (`documentation/layout.tsx`).
7. **App-detail tab bar** (`apps/[slug]/components/app-tabs.tsx`, 12 tabs; several umbrella sub-routes).
8. **Cluster-detail tab bar** (`clusters/[slug]/components/cluster-tabs.tsx`, 5 tabs).

**BROCS pillars (as shipped):**

| Pillar | Items |
|---|---|
| **BUILD** | Build (gateway, CTA disabled), Pipelines, Workflow Definitions |
| **RUN** | Deployments, Approvals, Agents, Skills, Tools, Jobs, Tasks, Functions (8 flat) |
| **OBSERVE** | Dashboard, Deployments, Agents, Jobs, Tasks, Functions, Events, Alerts, Platform Activity |
| **CONTROL** | Infrastructure (Clusters/Domains/Providers/Webhooks) · Governance (Members/Teams/Projects/Policies/Permissions/Tokens/Cost/Quotas/Audit) |
| **SECURE** | Zentinelle (gateway) |

**Dead nav source** (no importers — misleads nav edits): `settings-sidebar.tsx`, `settings-shell.tsx`, `settings-nav.ts`, `NavMain.tsx`, `NavProjects.tsx`.

## 2. Screen inventory (152 routes)

### Auth & entry
| Route | Purpose |
|---|---|
| `/` | redirect → `/dashboard` (authed) or `/auth/login` |
| `/auth/login` · `/auth/callback` · `/auth/invitation/[token]` | login · OAuth callback · accept invite |
| `/dashboard` | authed home; hosts onboarding wizard (`?onboarding=1`) |
| `/onboarding` | redirect → `/dashboard?onboarding=1` |
| `/orgs` | redirect → `/settings/organization` |

### BUILD
| Route | Purpose |
|---|---|
| `/build` | BUILD gateway (primary CTA disabled) |
| `/pipelines` · `/pipelines/[id]` · `/pipelines/[id]/secrets` | pipelines list · detail/runs · pipeline secrets |
| `/workflows` · `/workflows/new` · `/workflows/templates` · `/workflows/builder` · `/workflows/[slug]/builder` | workflow defs · create · templates · builder (new) · builder (existing) |

### RUN
| Route | Purpose |
|---|---|
| `/deployments` · `/deployments/[id]` | fleet deployments list · detail |
| `/approvals` · `/approvals/[id]` · `/approvals/secret/[id]` | approvals queue · request · secret-reveal request |
| `/agents` | agents list / dispatch |
| `/agents/gallery` | agent theatre (⚠ orphan; dup of `/observe/agents` Theatre) |
| `/agents/[task]/vnc` | pop-out noVNC for a task |
| `/agents/skills` · `/new` · `/import` · `/[id]` | **canonical** skills catalog + author/import/detail |
| `/agents/tools` · `/[slug]` | **canonical** tools catalog + detail |
| `/jobs` · `/tasks` · `/functions` | scheduled jobs · one-off tasks · functions (⚠ all-placeholder) |

### OBSERVE
| Route | Purpose |
|---|---|
| `/ops` | observe dashboard |
| `/observe/deployments` · `/observe/agents` · `/observe/jobs` · `/observe/tasks` · `/observe/functions` | per-primitive observability (⚠ mirror of RUN) |
| `/events` · `/alerts` · `/platform-activity` | event feed · alerts · Temporal engine observability |
| `/logs` · `/traces` · `/metrics` | log search · trace explorer · metrics (⚠ reachable only via action buttons / palette) |

### CONTROL — Infrastructure
| Route | Purpose |
|---|---|
| `/clusters` · `/clusters/[slug]` + `/status` `/health` `/activity` `/settings` | clusters list · overview + 4 tabs |
| `/domains` | custom/managed domains (canonical) |
| `/providers` | unified providers (`#identity`, `#source` tabs — post #889/#890) |
| `/webhooks` | org webhooks |

### CONTROL — Governance / Administration
| Route | Purpose |
|---|---|
| `/administration` (+ `/members` `/teams` `/projects` `/tokens` `/cost` `/quotas` `/metrics`) | admin landing + subnav pages |
| `/audit` | audit log |
| `/members` · `/teams` · `/teams/[slug]` · `/projects` · `/projects/[slug]` | canonical org-entity surfaces (+ detail) |
| `/tokens` · `/quotas` · `/cost` · `/environments` · `/previews` | canonical / top-level variants (some palette-only) |

### SECURE
| Route | Purpose |
|---|---|
| `/secure/zentinelle` | Zentinelle GRC gateway |

### Settings (subnav)
| Route | Purpose |
|---|---|
| `/settings` | settings landing card grid |
| `/settings/organization` · `/policies` · `/permissions` · `/profile` · `/security` · `/notifications` | org + user settings |
| `/settings/identity-provider` → `/providers#identity` · `/source-providers` → `/providers#source` · `/managed-domains` → `/domains` · `/members|teams|projects` → canonical | **redirects** (consolidation #889/#890/#413) |

### Apps (12-tab detail)
`/apps` · `/apps/new` (register wizard) · `/apps/[slug]` (Overview) — tabs: **Deployments** (umbrella: environments/jobs/commands) · **Workloads** (+ `[workloadSlug]`) · Topology · Observability · Console · Previews · Domains · **Secrets** (umbrella: tokens) · Security · Members · **Settings** (umbrella: config/manifest/webhooks/managed-services).

### Resources & Documentation (two competing hubs)
| Route | Purpose |
|---|---|
| `/resources` + `/docs` `/manifest` `/drivers` `/clusters` `/help` | Resources subnav — **the linked-but-thin hub** (`/resources/docs` = static link list) |
| `/downloads` | CLI/tooling downloads |
| `/documentation` + ~18 pages (introduction, quickstart, get-started, tutorials/[slug], cluster-prerequisites, custom-domains, source-providers, identity-providers, policies, webhooks, webhook-events, configuration, changelog, runbooks/{deploy,cluster-management,incident-response,agent-dispatch}) | **the real, fully-built docs system — but dark** (no nav entry except one deep link) |

## 3. Orphans (reachable only by typed URL / indirect) — 24

- **Fully unlinked subtrees:** `/playground` (+ history/starred/observability/topology) · `/skills` (+ new/[id]) — legacy dup of `/agents/skills` · `/forms` (+ new/[slug]/submit) · `/hooks` (dev hook demo, ≠ `/webhooks`) · `/agents/gallery`.
- **Palette-only (no sidebar):** `/cost` `/metrics` `/quotas` `/environments` `/previews`.
- **Action-button-only:** `/logs` `/traces` (from `/observe/deployments`).
- **Redirect-only targets:** `/orgs` `/onboarding` + the 6 settings redirects.
- **Index pages with no direct link:** `/resources`, `/administration` (subnavs jump to first child).

## 4. IA assessment — problems

- **A. Route duplication (same component, two URLs).** `/cost /quotas /metrics /tokens` ≡ `/administration/*`; `/skills/*` = stale dup of `/agents/skills/*` (create-flows already diverged: `createAgentSkill` vs `createSkill`); `/agents/gallery` = 3rd copy of `AgentTheatre`.
- **B. RUN ⇄ OBSERVE mirror.** Every primitive has a RUN list + an OBSERVE page whose "Fleet" tab re-queries the same `LIST_WORKLOADS`. Two nav trees that differ only by manage-vs-watch. Inconsistent: OBSERVE>Functions is real, RUN>Functions is all placeholder.
- **C. Two docs hubs.** Footer/Resources point at the thin `/resources/docs`; the real `/documentation/*` (~18 pages) is unlinked.
- **D. Orphaned subtrees in the prod bundle:** `/playground`, `/forms`, `/hooks`.
- **E. Dead-ends in core flows.** Cluster-preflight gate → `/clusters` empty state with no CTA (+ cites an opscode file path); **Agent Dispatch throws a hardcoded not-implemented into `console.error`** (no in-app way to start a task); register-cluster wants raw driver JSON validated only by `JSON.parse`; skill/tool/workflow not-found pages have no back link; `(app)` error boundary only offers "Try again"; pipeline Logs tab tells you to "select a run" but runs aren't clickable.
- **F. Weak/ambiguous screens.** Register wizard "skip" strands a non-deploying app; CI-push toggle silently downgrades; step-1 links the old redirect (destroys wizard state); "Deploy now" vs "Rebuild & deploy" unexplained; `learnMoreHref` (#356) honored in ~4 of 74 empty states.
- **G. Nav grouping noise.** RUN is a flat 8-item list; Skills/Tools are `/agents/*` children shown as flat siblings; Resources mirrors Clusters/Providers; dead nav source files.
- **H. Non-issues (don't "fix").** RUN `/deployments` (release history) vs OBSERVE `/observe/deployments` (fleet health) are genuinely distinct — relabel, don't merge. `/hooks` is **not** a webhooks variant — delete it, don't consolidate.

## 5. Recommended target IA (for discussion)

**One destination per primitive** — fold each `/observe/<primitive>` into the primitive's own page as tabs:
- **Agents** → Fleet | Dispatch | Theatre | Metrics | Logs | Traces (absorbs `/observe/agents` + `/agents/gallery`)
- **Deployments** → Releases | Health | Logs | Traces
- **Jobs / Tasks / Functions** → List | Observability (gate Functions until it has a backing query)
- **OBSERVE** keeps only cross-cutting signals: **Dashboard · Events · Alerts · Platform Activity**.

**Proposed top-level nav:**

| Pillar | Contents | Purpose |
|---|---|---|
| BUILD | Pipelines, Workflows (+ Build gateway, honestly "coming soon") | author CI/build/workflow definitions |
| RUN | Deployments, Approvals, **Agents** (Skills/Tools as tabs *within* Agents), Jobs, Tasks, (Functions when live) | operate workloads — single manage+watch surface each |
| OBSERVE | Dashboard, Events, Alerts, Platform Activity | cross-cutting signals only |
| CONTROL | Infrastructure (Clusters/Domains/Providers/Webhooks) · Governance (…) | org + infra administration |
| SECURE | Zentinelle | security/GRC |

**Canonicalize via server redirects** (matching the existing `/orgs`, `/settings/*` pattern): `/cost /quotas /metrics` → `/administration/*`; `/administration/tokens` → `/tokens`; delete `/skills/*` `/agents/gallery` `/hooks` (redirect `/skills`→`/agents/skills`); decide `/playground` + `/forms` explicitly (wire in or delete). **Docs:** point Docs links at `/documentation`, retire `/resources/docs`. **Make `learnMoreHref` a lint, not an aspiration.**

## 6. Filed issues

**`[IA]` (this audit) — #892–#919:** RUN/OBSERVE merge (892) · `/cost`-`/admin` canonicalize (893) · surface `/documentation` (894) · cluster-preflight dead-end (895) · **Agent Dispatch no-op (896)** · `/playground` fate (897) · delete `/skills/*` (898) · not-found back links (899) · wizard→source-providers redirect (900) · "skip" deploy signpost (901) · register-cluster JSON UX (902) · `learnMoreHref` sweep (903) · remove `/hooks` (904) · drop `/agents/gallery` (905) · gate RUN>Functions (906) · `/forms` fate (907) · CI-push toggle (908) · error-boundary escape (909) · pipeline Logs dead loop (910) · `/build` CTA honesty (911) · pipeline placeholder tabs (912) · delete dead nav source (913) · Deploy-now vs Rebuild (914) · RUN nav restructure (915) · trim Resources (916) · disambiguate Deployments labels (917) · cross-link domains (918) · `/environments` `/previews` fate (919).

**Earlier UI bugs — #885–#891:** Select.Item profile crash (885) · notifications channel enum (886) · providers filter (887) · managed-domains dup (888) · source providers→providers (889) · identity providers→providers (890) · agentTaskLogs namespace/selector (891).
