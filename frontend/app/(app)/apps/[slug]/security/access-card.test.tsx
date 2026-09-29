import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessCard } from "./access-card";

// Who may enter an app behind central auth (#2132).

const state = vi.hoisted(() => ({ access: null as unknown, sent: [] as unknown[] }));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({ data: { astroliftAppAccess: state.access }, loading: false }),
  useLazyQuery: () => [vi.fn(), { data: undefined }],
  useMutation: () => [
    vi.fn(async (opts: { variables: unknown }) => {
      state.sent.push(opts.variables);
      return { data: { setAppAccess: { ok: true, errors: [] } } };
    }),
    { loading: false },
  ],
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, granted: new Set(["app.access"]), loading: false }),
}));

const open = {
  appSlug: "veruus-demo",
  groups: [],
  users: [],
  restricted: false,
  managedByManifest: false,
  enforcedOn: ["astrolift-conflict"],
};

beforeEach(() => {
  state.access = open;
  state.sent = [];
});

describe("AccessCard (#2132)", () => {
  it("says an app with no rule is open to every signed-in user", () => {
    render(<AccessCard appSlug="veruus-demo" />);

    expect(screen.getByText(/Every user who can sign in/)).toBeInTheDocument();
  });

  it("saves the groups an operator adds", async () => {
    render(<AccessCard appSlug="veruus-demo" />);
    fireEvent.change(screen.getByLabelText("Groups"), { target: { value: "veruus" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Save access" }));

    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0]).toEqual({
      input: { appSlug: "veruus-demo", groups: ["veruus"], users: [] },
    });
  });

  it("refuses a user that is not an email", () => {
    render(<AccessCard appSlug="veruus-demo" />);
    fireEvent.change(screen.getByLabelText("Users (email)"), { target: { value: "bob" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[1]);

    expect(screen.getByText("Enter an email address.")).toBeInTheDocument();
  });

  it("only shows a rule set in astrolift.toml", () => {
    state.access = { ...open, groups: ["veruus"], restricted: true, managedByManifest: true };
    render(<AccessCard appSlug="veruus-demo" />);

    expect(screen.getByText("set in astrolift.toml")).toBeInTheDocument();
    expect(screen.getByText("veruus")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save access" })).not.toBeInTheDocument();
  });
});
