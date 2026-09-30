import type { ReactNode } from "react";

import { screen } from "@testing-library/react";
import { renderWithIntl as render } from "@/test/render-with-intl";
import { describe, expect, it, vi } from "vitest";

import { CronjobHome } from "./cronjob-home";

/**
 * A cronjob's page used to ask for the *app's* newest runs and keep its
 * own in the browser (#1512). A job firing less often than its neighbours
 * fell off the end of that fetch, so its page said "no runs recorded yet"
 * while its runs sat one query away — indistinguishable from a job that
 * had genuinely never fired, on the page whose whole purpose is telling
 * an operator whether it fired.
 *
 * What fixes that is the request, not the render, so that is what these
 * hold: the workload reaches the server, and nothing filters afterwards.
 */

type Vars = Record<string, unknown>;

const state = vi.hoisted(() => ({ calls: [] as { op: string; variables: Vars }[] }));

vi.mock("@apollo/client/react", () => ({
  // Older pages load through the client, on the reader's scroll only.
  useApolloClient: () => ({ query: vi.fn().mockResolvedValue({ data: undefined }) }),
  useQuery: (
    doc: { definitions?: { kind: string; name?: { value: string } }[] },
    options?: { variables?: Vars }
  ) => {
    const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
    state.calls.push({ op, variables: options?.variables ?? {} });
    return {
      data: {
        astroliftScheduledJobRunsPage: {
          items: [
            {
              id: "run-1",
              registeredAppSlug: "billing",
              environmentName: "production",
              workloadSlug: "weekly",
              k8sJobName: "weekly-000",
              status: "succeeded",
              startedAt: "2026-08-20T02:00:00Z",
              endedAt: "2026-08-20T02:00:12Z",
              durationSeconds: 12,
              exitCode: 0,
              logExcerpt: "",
              output: "",
              createdAt: "2026-08-20T02:00:00Z",
            },
          ],
          nextCursor: null,
          totalCount: 1,
        },
      },
      previousData: undefined,
      loading: false,
      error: undefined,
      refetch: vi.fn().mockResolvedValue({}),
    };
  },
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("../app-tabs", () => ({ AppTabs: () => null }));

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

const workload = {
  slug: "weekly",
  schedule: "0 2 * * 0",
  concurrencyPolicy: "Forbid",
} as never;

function renderHome() {
  state.calls.length = 0;
  return render(<CronjobHome slug="billing" name="Weekly digest" workload={workload} />);
}

describe("CronjobHome run history", () => {
  it("asks the server for this workload's runs, not the app's", () => {
    renderHome();
    const call = state.calls.find((c) => c.op === "ListScheduledJobRunsPage");
    expect(call).toBeDefined();
    // Both, and for a reason: workload slugs are unique within an app,
    // not across the org, so `workloadSlug` alone would match a
    // same-named job in a sibling app.
    expect(call?.variables).toMatchObject({ appSlug: "billing", workloadSlug: "weekly" });
  });

  it("does not ask for the deprecated unpaginated field", () => {
    // The flat `astroliftScheduledJobRuns` under a hard limit is what
    // made the history a client-side slice in the first place.
    renderHome();
    expect(state.calls.map((c) => c.op)).not.toContain("ListScheduledJobRuns");
  });

  it("renders the run the server returned", () => {
    renderHome();
    expect(screen.getByText("Succeeded")).toBeInTheDocument();
  });
});
