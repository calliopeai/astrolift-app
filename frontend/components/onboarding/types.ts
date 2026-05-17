/**
 * Shared types for the first-run onboarding wizard (#452).
 *
 * Deliberately tiny — most state is per-step and lives in the
 * top-level wizard component; this module exists so step children
 * can import the union types without reaching across the directory.
 */

export type OnboardingStep = 1 | 2 | 3 | 4;

/**
 * Wizard-level deploy strategy intent. Captured by step 4 and
 * surfaced in the review summary; persisted alongside the new app
 * via the manifest workflow rather than a dedicated backend field
 * (no `RegisterAppInput.deploy_strategy` exists today — tracked
 * via from:backend follow-up).
 */
export type DeployStrategy = "blue-green" | "canary" | "rolling";

/**
 * Shape of the wizard's accumulated state. Each step writes into it
 * via `setState({ ...prev, ... })`; the submit handler reads the
 * whole object and chains create-team -> create-project ->
 * optionally register-app -> mark-onboarding-complete.
 */
export interface OnboardingWizardState {
  step: OnboardingStep;

  // Step 1 — team
  teamName: string;
  teamSlug: string;
  teamSlugTouched: boolean;

  // Step 2 — project
  projectName: string;
  projectSlug: string;
  projectSlugTouched: boolean;
  projectDescription: string;

  // Step 3 — repo (all optional; the wizard skips persistence if
  // the operator left the picker untouched or chose Skip)
  sourceConnectionId: string;
  sourceRepo: string;
  sourceUrl: string;
  sourceDefaultBranch: string;

  // Step 4 — deploy strategy
  deployStrategy: DeployStrategy;

  // Outcome flags filled in by the submit handler so the FE can
  // render the right closing toast / spotlight handoff.
  submitting: boolean;
  submitError: string | null;
}

export const DEFAULT_ONBOARDING_STATE: OnboardingWizardState = {
  step: 1,
  teamName: "",
  teamSlug: "",
  teamSlugTouched: false,
  projectName: "",
  projectSlug: "",
  projectSlugTouched: false,
  projectDescription: "",
  sourceConnectionId: "",
  sourceRepo: "",
  sourceUrl: "",
  sourceDefaultBranch: "main",
  deployStrategy: "rolling",
  submitting: false,
  submitError: null,
};

/**
 * Slugify a free-form name into a kebab-case slug suitable for
 * Team.slug / Project.slug. Stays in lock-step with the slug rules
 * the backend enforces on those models (lowercase, alphanumerics +
 * hyphens, no leading / trailing / consecutive hyphens).
 */
export function deriveSlug(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 60);
}
