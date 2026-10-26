import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";
import { fireEvent, render as renderView, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessCard } from "./access-card";

function render(view: React.ReactNode) {
  return renderView(
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      {view}
    </NextIntlClientProvider>
  );
}

// Who may enter an app behind central auth (#2132).

const state = vi.hoisted(() => ({
  access: null as unknown,
  sent: [] as unknown[],
  publicReply: null as unknown,
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({ data: { astroliftAppAccess: state.access }, loading: false }),
  useLazyQuery: () => [vi.fn(), { data: undefined }],
  useMutation: () => [
    vi.fn(async (opts: { variables: unknown }) => {
      state.sent.push(opts.variables);
      return {
        data: {
          setAppAccess: { ok: true, errors: [] },
          setAppEdgePublic: state.publicReply,
        },
      };
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
  edgePublic: false,
  publicStates: [{ clusterSlug: "astrolift-conflict", state: "gated" }],
};

beforeEach(() => {
  state.access = open;
  state.sent = [];
  state.publicReply = {
    ok: true,
    errors: [],
    data: { appSlug: "veruus-demo", edgePublic: true, publicStates: [] },
  };
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

describe("the public switch (#2504)", () => {
  it("asks before making an app public, then sends the switch", async () => {
    render(<AccessCard appSlug="veruus-demo" />);
    fireEvent.click(screen.getByRole("button", { name: "Make public…" }));

    expect(await screen.findByText("Make veruus-demo public?")).toBeInTheDocument();
    expect(state.sent).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Make public" }));

    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0]).toEqual({ input: { appSlug: "veruus-demo", public: true } });
  });

  it("puts a public app behind the sign-in without asking", async () => {
    state.access = {
      ...open,
      edgePublic: true,
      publicStates: [{ clusterSlug: "astrolift-conflict", state: "public" }],
    };
    render(<AccessCard appSlug="veruus-demo" />);

    expect(screen.getByText("answers without the sign-in")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Put behind the sign-in" }));

    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0]).toEqual({ input: { appSlug: "veruus-demo", public: false } });
  });

  it("cannot make an app with an access list public", () => {
    state.access = { ...open, groups: ["veruus"], restricted: true };
    render(<AccessCard appSlug="veruus-demo" />);

    expect(screen.getByRole("button", { name: "Make public…" })).toBeDisabled();
    expect(screen.getByText(/Clear the access list first/)).toBeInTheDocument();
  });

  it("shows the server's refusal under the switch", async () => {
    state.publicReply = {
      ok: false,
      errors: [
        { code: "PERMISSION_DENIED", message: "this action needs a person", field: "public" },
      ],
      data: null,
    };
    render(<AccessCard appSlug="veruus-demo" />);
    fireEvent.click(screen.getByRole("button", { name: "Make public…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Make public" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("this action needs a person");
    expect(screen.queryByText("Make veruus-demo public?")).not.toBeInTheDocument();
  });
});
