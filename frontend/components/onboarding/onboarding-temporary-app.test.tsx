import { readFileSync } from "node:fs";

import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { fireEvent, render, renderHook, screen } from "@testing-library/react";
import { buildSchema, execute, parse, validate } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TemporaryAppPolicyState } from "@/components/screens/apps/temporary-app-model";
import en from "@/messages/en.json";

import { ACTIONS, REPO_DATA, TEMPORARY_ONLY_POLICY, TEMPORARY_POLICY } from "./fixtures";
import { OnboardingWizard } from "./OnboardingWizard";
import { DEFAULT_ONBOARDING_STATE, type OnboardingWizardState } from "./types";
import { useOnboardingActions } from "./use-onboarding-actions";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const copy = en.apps.temporaryCreation;

/** Team and project filled in, at the repo step, with a connection chosen. */
const FILLED: OnboardingWizardState = {
  ...DEFAULT_ONBOARDING_STATE,
  step: 3,
  teamName: "Commerce",
  teamSlug: "commerce",
  projectName: "Storefront",
  projectSlug: "storefront",
  sourceConnectionId: "c1",
};
const PICKED: OnboardingWizardState = { ...FILLED, sourceRepo: "conflict/checkout" };

function Wizard({
  initial,
  temporary,
}: {
  initial: OnboardingWizardState;
  temporary: TemporaryAppPolicyState;
}) {
  const [state, setState] = React.useState(initial);
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <OnboardingWizard
        open
        onOpenChange={() => {}}
        state={state}
        setState={setState}
        actions={ACTIONS}
        repo={REPO_DATA}
        temporary={temporary}
      />
    </NextIntlClientProvider>
  );
}

const next = () => screen.getByRole("button", { name: en.onboarding.nextCta });

beforeEach(() => vi.clearAllMocks());

describe("the onboarding repo step's app lifetime (#2433)", () => {
  it("asks for it only once a repo is picked, so the step stays optional", () => {
    const view = render(<Wizard initial={FILLED} temporary={TEMPORARY_ONLY_POLICY} />);
    expect(screen.queryByLabelText(copy.ttl)).toBeNull();
    expect(next().hasAttribute("disabled")).toBe(false);
    view.unmount();

    render(<Wizard initial={FILLED} temporary={{ rules: null, loading: false, failed: true }} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(next().hasAttribute("disabled")).toBe(false);
  });

  it("holds Next to the organization's policy and carries the lifetime into the review", () => {
    render(<Wizard initial={PICKED} temporary={TEMPORARY_ONLY_POLICY} />);
    const lifetime = screen.getByLabelText(copy.ttl);
    expect(screen.getByText(copy.required)).toBeTruthy();
    expect(next().hasAttribute("disabled")).toBe(true);

    fireEvent.change(lifetime, { target: { value: "999d" } });
    expect(lifetime.getAttribute("aria-invalid")).toBe("true");
    expect(next().hasAttribute("disabled")).toBe(true);

    fireEvent.change(lifetime, { target: { value: "4h" } });
    expect(next().hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByText(copy.deleteWarning)).toBeTruthy();

    fireEvent.click(next());
    expect(screen.getByRole("button", { name: en.onboarding.finishCta })).toBeTruthy();
    const term = screen.getByText(copy.ttl);
    expect(term.nextElementSibling?.textContent).toBe("4h");
    expect(screen.getByText(copy.deleteData).nextElementSibling?.textContent).toBe(
      copy.deleteWarning
    );
  });

  it("lets a permanent app through when the policy allows one", () => {
    render(<Wizard initial={PICKED} temporary={TEMPORARY_POLICY} />);
    expect(screen.getByLabelText(copy.ttl)).toBeTruthy();
    expect(next().hasAttribute("disabled")).toBe(false);
  });

  it("waits for the policy, and offers a retry when it cannot be read", () => {
    const view = render(
      <Wizard initial={PICKED} temporary={{ rules: null, loading: true, failed: false }} />
    );
    expect(screen.getByRole("status").textContent).toBe(copy.loading);
    expect(next().hasAttribute("disabled")).toBe(true);
    view.unmount();

    const onRetry = vi.fn();
    const failed = render(
      <Wizard initial={PICKED} temporary={{ rules: null, loading: false, failed: true, onRetry }} />
    );
    expect(screen.getByRole("alert").textContent).toBe(copy.unavailable);
    expect(next().hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: copy.retry }));
    expect(onRetry).toHaveBeenCalledOnce();

    // The retry reads it: Next follows the policy, not the form state alone.
    failed.rerender(<Wizard initial={PICKED} temporary={TEMPORARY_POLICY} />);
    expect(next().hasAttribute("disabled")).toBe(false);
  });

  it("lists no lifetime in the review when no app is registered", () => {
    render(
      <Wizard initial={{ ...FILLED, step: 4, expiresIn: "4h" }} temporary={TEMPORARY_POLICY} />
    );
    expect(screen.getByRole("button", { name: en.onboarding.finishCta })).toBeTruthy();
    expect(screen.queryByText(copy.ttl)).toBeNull();
    expect(screen.queryByText(copy.deleteData)).toBeNull();
  });
});

const ORG = {
  id: "10000000-0000-4000-8000-000000000001",
  slug: "acme",
  name: "Acme",
  onboardingCompletedAt: null,
  createdAt: "2026-10-22T00:00:00Z",
  updatedAt: "2026-10-22T00:00:00Z",
  deletedAt: null,
};
const TEAM = {
  id: "10000000-0000-4000-8000-000000000002",
  slug: "commerce",
  name: "Commerce",
  organization: ORG,
  createdAt: "2026-10-22T00:00:00Z",
  updatedAt: "2026-10-22T00:00:00Z",
  deletedAt: null,
};
const PROJECT = {
  ...TEAM,
  id: "10000000-0000-4000-8000-000000000003",
  slug: "storefront",
  name: "Storefront",
  team: TEAM,
};
const APP = {
  id: "10000000-0000-4000-8000-000000000004",
  slug: "storefront",
  name: "Storefront",
  provisioningStatus: "pending",
  sourceRepo: "conflict/checkout",
  manifestPath: "astrolift.toml",
  teamSlug: "commerce",
  projectSlug: "storefront",
  requiresApproval: false,
  approverTeamId: null,
  approverUserIds: [],
  minimumApprovals: 1,
};
const REFUSAL =
  "this organization lets members create temporary apps only: set expiresIn or expiresAt";

/** The onboarding writes over HTTP, answered by the real SDL. */
function server() {
  const requests: Array<{ operationName: string; variables: Record<string, unknown> }> = [];
  let registered: Record<string, unknown> = { ok: true, errors: [], data: APP };
  const fetch = vi.fn(async (_url: RequestInfo | URL, options?: RequestInit) => {
    const request = JSON.parse(String(options?.body));
    requests.push(request);
    const document = parse(request.query);
    expect(validate(schema, document)).toEqual([]);
    const result = await execute({
      schema,
      document,
      variableValues: request.variables,
      rootValue: {
        createTeam: () => ({ ok: true, errors: [], data: TEAM }),
        createProject: () => ({ ok: true, errors: [], data: PROJECT }),
        registerApp: () => registered,
        markOnboardingComplete: () => ({
          ok: true,
          errors: [],
          data: { alreadyCompleted: false, organization: ORG },
        }),
        astroliftOrganizations: [ORG],
        astroliftTeams: [TEAM],
      },
    });
    expect(result.errors).toBeUndefined();
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({ uri: "https://fixture.invalid/app/gql/config/", fetch }),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        {children}
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  const { result } = renderHook(() => useOnboardingActions(ORG.id), { wrapper });
  return {
    submit: (state: OnboardingWizardState) => result.current.submit(state),
    refuse: (response: Record<string, unknown>) => {
      registered = response;
    },
    input: () =>
      requests.filter((r) => r.operationName === "RegisterApp").map((r) => r.variables.input),
    operations: () => requests.map((r) => r.operationName),
  };
}

describe("onboarding registration over the real SDL (#2433)", () => {
  it("registers the picked repo's app with the lifetime the repo step set", async () => {
    const s = server();
    const state = { ...PICKED, expiresIn: " 4h ", deleteDataOnExpiry: true };
    expect(await s.submit(state)).toEqual({ ok: true });
    expect(s.input()).toEqual([
      {
        projectId: PROJECT.id,
        expiresIn: "4h",
        expiresAt: null,
        deleteDataOnExpiry: true,
        name: "Storefront",
        slug: "storefront",
        description: null,
        sourceKind: "github",
        sourceRepo: "conflict/checkout",
        sourceUrl: null,
        defaultBranch: "main",
      },
    ]);
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it("sends no expiry for an app left permanent, and registers nothing without a repo", async () => {
    const s = server();
    await s.submit({ ...PICKED, deleteDataOnExpiry: true });
    expect(s.input()).toHaveLength(1);
    const [input] = s.input() as Array<Record<string, unknown>>;
    expect(Object.keys(input)).not.toContain("expiresIn");
    expect(Object.keys(input)).not.toContain("expiresAt");
    expect(Object.keys(input)).not.toContain("deleteDataOnExpiry");

    const without = server();
    expect(await without.submit({ ...FILLED, expiresIn: "4h" })).toEqual({ ok: true });
    expect(without.operations()).not.toContain("RegisterApp");
  });

  it("reports a registration the server refuses instead of finishing silently", async () => {
    const s = server();
    s.refuse({
      ok: false,
      errors: [{ code: "PERMISSION_DENIED", message: REFUSAL, field: "expiresIn" }],
      data: null,
    });
    expect(await s.submit(PICKED)).toEqual({ ok: true });
    expect(toast.warning).toHaveBeenCalledWith(REFUSAL);
    // The team and project landed, so onboarding still completes.
    expect(s.operations()).toContain("MarkOnboardingComplete");

    vi.clearAllMocks();
    s.refuse({ ok: false, errors: [], data: null });
    await s.submit(PICKED);
    expect(toast.warning).toHaveBeenCalledWith(en.onboarding.toast.appWarning);
  });
});
