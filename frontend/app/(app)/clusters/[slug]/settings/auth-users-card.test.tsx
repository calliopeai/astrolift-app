import { renderWithIntl as render } from "@/test/render-with-intl";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthUsersCard } from "./auth-users-card";

// The users of a cluster's central auth, from the UI (#2131).

const state = vi.hoisted(() => ({
  view: null as unknown,
  sent: [] as unknown[],
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: { astroliftClusterAuthUsers: state.view },
    loading: false,
    refetch: vi.fn(),
  }),
  useMutation: () => [
    vi.fn(async (opts: { variables: unknown }) => {
      state.sent.push(opts.variables);
      const ok = { ok: true, errors: [] };
      return {
        data: {
          createClusterAuthUser: ok,
          setClusterAuthUserPassword: ok,
          setClusterAuthUserEnabled: ok,
          deleteClusterAuthUser: ok,
          setClusterAuthUserGroups: ok,
          createClusterAuthGroup: ok,
          resetClusterAuthUserPassword: ok,
        },
      };
    }),
    { loading: false },
  ],
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const supported = {
  supported: true,
  reason: "",
  provider: "Amazon Cognito",
  source: {
    providerPluginId: "plugin-literal",
    providerPoolId: "pool-literal",
    sourceVersion: "revision-literal",
  },
  reachNote: "A user of this pool can sign in to every app on the cluster.",
  groups: ["veruus"],
  users: [
    {
      username: "u1",
      providerUserId: "subject-literal",
      email: "veruus-user@example.com",
      enabled: true,
      status: "CONFIRMED",
      createdAt: null,
      groups: ["veruus"],
    },
  ],
};

beforeEach(() => {
  state.view = supported;
  state.sent = [];
});

describe("AuthUsersCard (#2131)", () => {
  it("lists users with their groups and says what a user can reach", () => {
    render(<AuthUsersCard clusterId="c1" />);

    expect(screen.getByText("veruus-user@example.com")).toBeInTheDocument();
    expect(screen.getByText("Amazon Cognito")).toBeInTheDocument();
    expect(screen.getByText(/every app on the cluster/)).toBeInTheDocument();
  });

  it("explains an identity provider it cannot manage", () => {
    state.view = {
      ...supported,
      supported: false,
      reason: "central auth does not sign in through Amazon Cognito",
      users: [],
    };
    render(<AuthUsersCard clusterId="c1" />);

    expect(screen.getByText(/does not sign in through Amazon Cognito/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add user" })).not.toBeInTheDocument();
  });

  it("adding a user without a password leaves the invitation to the provider", async () => {
    render(<AuthUsersCard clusterId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Add user" }));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add user" }).at(-1)!);

    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0]).toEqual({
      input: {
        clusterId: "c1",
        expectedSource: supported.source,
        email: "new@example.com",
        password: null,
        permanent: false,
        groups: [],
      },
    });
  });

  it("disables a user from the row's menu", async () => {
    render(<AuthUsersCard clusterId="c1" />);
    // Row actions sit in `⋯` (spec 44 §5.1); Radix opens on pointer down.
    const menu = screen.getByRole("button", { name: /row actions/i });
    fireEvent.pointerDown(menu, { button: 0, ctrlKey: false, pointerType: "mouse" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Disable" }));

    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0]).toEqual({
      input: {
        clusterId: "c1",
        expectedSource: supported.source,
        expectedUserId: "subject-literal",
        username: "u1",
        enabled: false,
      },
    });
  });
});
