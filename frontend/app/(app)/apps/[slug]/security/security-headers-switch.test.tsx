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

const state = vi.hoisted(() => ({
  access: null as unknown,
  sent: [] as unknown[],
  reply: null as unknown,
  granted: true,
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({ data: { astroliftAppAccess: state.access }, loading: false }),
  useLazyQuery: () => [vi.fn(), { data: undefined }],
  useMutation: () => [
    vi.fn(async (opts: { variables: unknown }) => {
      state.sent.push(opts.variables);
      return { data: { setAppSecurityHeaders: state.reply } };
    }),
    { loading: false },
  ],
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => state.granted, loading: false }),
}));

const open = {
  appSlug: "demo",
  groups: [],
  users: [],
  restricted: false,
  managedByManifest: false,
  enforcedOn: [],
  edgePublic: false,
  publicStates: [],
  securityHeaders: false,
};

beforeEach(() => {
  state.access = open;
  state.sent = [];
  state.granted = true;
  state.reply = { ok: true, errors: [], data: { appSlug: "demo", securityHeaders: true } };
});

describe("the security headers switch", () => {
  it("is off by default and enables without asking", async () => {
    render(<AccessCard appSlug="demo" />);

    expect(screen.getByText("Security headers")).toBeInTheDocument();
    expect(screen.getByText("Off")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));

    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0]).toEqual({ input: { appSlug: "demo", enabled: true } });
  });

  it("disables without asking", async () => {
    state.access = { ...open, securityHeaders: true };
    render(<AccessCard appSlug="demo" />);

    expect(screen.getByText("On")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));

    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0]).toEqual({ input: { appSlug: "demo", enabled: false } });
  });

  it("shows the server's refusal under the button", async () => {
    state.reply = {
      ok: false,
      errors: [{ code: "NOT_FOUND", message: "app not found", field: "appSlug" }],
      data: null,
    };
    render(<AccessCard appSlug="demo" />);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("app not found");
  });

  it("shows the state only without app.access", () => {
    state.granted = false;
    render(<AccessCard appSlug="demo" />);

    expect(screen.getByText("Security headers")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable" })).not.toBeInTheDocument();
  });
});
