import { readFileSync } from "node:fs";

import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildSchema, execute, parse, validate } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import en from "@/messages/en.json";

import { OnboardingHost } from "./onboarding-host";

const router = { replace: vi.fn() };
const noQuery = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/dashboard",
  useSearchParams: () => noQuery,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const copy = en.apps.temporaryCreation;
const AT = "2026-10-22T00:00:00Z";

const ORG = {
  id: "10000000-0000-4000-8000-000000000001",
  slug: "acme",
  name: "Acme",
  website: "",
  scimEnabled: false,
  auditLogRetentionDays: 90,
  appearanceDefault: {},
  appearanceLocked: false,
  restrictedSettingsDefault: "",
  previewMaxActiveDefault: 3,
  logRetentionDaysDefault: 30,
  allowUserProfileEdit: true,
  allowStepUpPin: false,
  trustedDomainSignup: false,
  onboardingCompletedAt: null as string | null,
  createdAt: AT,
  updatedAt: AT,
  deletedAt: null,
};
const TEAM = {
  id: "10000000-0000-4000-8000-000000000002",
  slug: "commerce",
  name: "Commerce",
  organization: ORG,
  createdAt: AT,
  updatedAt: AT,
  deletedAt: null,
};
const PROJECT = {
  ...TEAM,
  id: "10000000-0000-4000-8000-000000000003",
  slug: "storefront",
  name: "Storefront",
  team: TEAM,
};
const CONNECTION = {
  id: "10000000-0000-4000-8000-000000000005",
  kind: "github_app",
  name: "conflict",
  displayName: "GitHub · conflict",
  accountLogin: "conflict",
  installationId: "1",
  apiBaseUrl: "",
  oauthClientId: "",
  appClientId: "",
  needsClientId: false,
  oauthRedirectUri: "",
  repoVisibilityScopes: [],
  isOauthAppConfig: false,
  isActive: true,
  tokenExpiresAt: null,
  lastUsedAt: null,
  createdAt: AT,
  updatedAt: AT,
  isPersonal: false,
  userUsername: null,
  parentOauthAppId: null,
};
const REPO = {
  fullName: "conflict/checkout",
  name: "checkout",
  description: "",
  defaultBranch: "main",
  visibility: "private",
  cloneUrlHttps: "https://github.com/conflict/checkout.git",
  cloneUrlSsh: "git@github.com:conflict/checkout.git",
  webUrl: "https://github.com/conflict/checkout",
  isArchived: false,
  isFork: false,
  pushedAt: null,
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

/** The dashboard's reads and the onboarding writes over HTTP, answered by
 *  the real SDL. Members of this organization may create temporary apps only,
 *  and this person cannot update the organization. */
function renderHost({ onboarded }: { onboarded: boolean }) {
  const requests: Array<{ operationName: string; variables: Record<string, unknown> }> = [];
  const organization = { ...ORG, onboardingCompletedAt: onboarded ? AT : null };
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
        astroliftOrganizations: [organization],
        astroliftTeams: [],
        astroliftMyPermissions: ["app.read", "app.create", "team.create", "project.create"],
        astroliftSourceConnections: [CONNECTION],
        astroliftAvailableRepos: {
          repos: [REPO],
          errorCode: null,
          errorMessage: null,
          recoverable: true,
        },
        astroliftServerInfo: { capabilities: ["apps.temporary_expiry"] },
        astroliftTemporaryAppPolicy: {
          minTtlSeconds: 900,
          maxTtlSeconds: 7776000,
          temporaryOnlyForMembers: true,
          ttlRequiredSurfaces: [],
          dataGraceDays: 7,
        },
        createTeam: { ok: true, errors: [], data: TEAM },
        createProject: { ok: true, errors: [], data: PROJECT },
        registerApp: { ok: true, errors: [], data: APP },
        markOnboardingComplete: {
          ok: true,
          errors: [],
          data: {
            alreadyCompleted: false,
            organization: { ...organization, onboardingCompletedAt: AT },
          },
        },
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
  render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ActiveOrgProvider>
          <OnboardingHost />
        </ActiveOrgProvider>
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  return { operations: () => requests.map((r) => r.operationName), requests };
}

const next = () => screen.getByRole("button", { name: en.onboarding.nextCta });

beforeEach(() => vi.clearAllMocks());

describe("the dashboard's onboarding host and the app lifetime policy (#2433)", () => {
  it("does not read the policy while the wizard is closed", async () => {
    const host = renderHost({ onboarded: true });
    await waitFor(() =>
      expect(host.operations()).toEqual(
        expect.arrayContaining(["GetOnboardingState", "ListOrganizations", "GetMyPermissions"])
      )
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(host.operations()).not.toContain("TemporaryAppCapabilities");
    expect(host.operations()).not.toContain("TemporaryAppPolicy");
  });

  it("holds the picked repo's app to the policy it reads once the wizard opens", async () => {
    const host = renderHost({ onboarded: false });
    fireEvent.change(await screen.findByLabelText(en.onboarding.welcome.teamNameLabel), {
      target: { value: "Commerce" },
    });
    fireEvent.click(next());
    fireEvent.change(screen.getByLabelText(en.onboarding.project.projectNameLabel), {
      target: { value: "Storefront" },
    });
    fireEvent.click(next());

    fireEvent.click(await screen.findByRole("combobox", { name: en.onboarding.repo.repoLabel }));
    fireEvent.click(await screen.findByRole("option", { name: REPO.fullName }));

    // Members may create temporary apps only, so Next waits for a lifetime.
    const lifetime = await screen.findByLabelText(copy.ttl);
    expect(screen.getByText(copy.required)).toBeTruthy();
    expect(next().hasAttribute("disabled")).toBe(true);
    expect(host.operations()).toContain("TemporaryAppPolicy");

    fireEvent.change(lifetime, { target: { value: "4h" } });
    fireEvent.click(next());
    fireEvent.click(screen.getByRole("button", { name: en.onboarding.finishCta }));

    await waitFor(() => expect(host.operations()).toContain("MarkOnboardingComplete"));
    const registered = host.requests.filter((r) => r.operationName === "RegisterApp");
    expect(registered.map((r) => r.variables.input)).toEqual([
      expect.objectContaining({
        sourceRepo: REPO.fullName,
        expiresIn: "4h",
        expiresAt: null,
        deleteDataOnExpiry: false,
      }),
    ]);
  });
});
