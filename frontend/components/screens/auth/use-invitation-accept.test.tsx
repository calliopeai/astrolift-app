import { act, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { signInReturnPath } from "@/lib/auth/sign-in-return";

import type { InvitationSummary } from "./InvitationAccept";
import {
  type InvitationPreview,
  invitationView,
  useInvitationAccept,
} from "./use-invitation-accept";

/**
 * Live on CONFLICT a signed-out invitee could only press Accept, which sent
 * the authenticated mutation: a getToken 401 and a GraphQL 401 per press, and
 * nothing on screen. The page now reads the public preview and offers sign-in.
 */
const mocks = vi.hoisted(() => ({ mutate: vi.fn(), push: vi.fn() }));
vi.mock("@apollo/client/react", () => ({
  useMutation: () => [mocks.mutate, { loading: false }],
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));

const TOKEN = "alft_example-invitation_Token09";
const INVITATION: InvitationSummary = {
  organizationName: "Acme Robotics",
  inviterName: "Ada Lovelace",
  roleName: "Developer",
  emailHint: "n•••@acme.example",
  expiresAt: "2026-10-23T18:30:00Z",
};
const SIGNED_OUT: InvitationPreview = {
  status: "pending",
  invitation: INVITATION,
  viewer: { signedIn: false },
};
const AS_INVITEE: InvitationPreview = {
  ...SIGNED_OUT,
  viewer: { signedIn: true, email: "new.person@acme.example", isInvitee: true },
};

function wrapper({ children }: React.PropsWithChildren) {
  return (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      {children}
    </NextIntlClientProvider>
  );
}

function answering(...previews: (InvitationPreview | number)[]) {
  const fetch = vi.fn(async () => {
    const next = previews.length > 1 ? previews.shift()! : previews[0];
    return typeof next === "number"
      ? new Response("{}", { status: next })
      : new Response(JSON.stringify(next), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const urls = (fetch: ReturnType<typeof vi.fn>) => fetch.mock.calls.map(([url]) => String(url));

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.unstubAllGlobals());

describe("the invitation page", () => {
  it("shows a signed-out invitee the invitation and sends nothing that needs a session", async () => {
    const fetch = answering(SIGNED_OUT);
    const { result } = renderHook(() => useInvitationAccept(TOKEN), { wrapper });

    await waitFor(() => expect(result.current.view.kind).toBe("signIn"));
    expect(result.current.view).toEqual({ kind: "signIn", invitation: INVITATION });
    expect(urls(fetch)).toEqual(["/app/auth1/invitation-preview"]);
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({ token: TOKEN });
    expect(init.headers).toMatchObject({ "X-Platform": "web" });
    expect(init.headers).not.toHaveProperty("Authorization");
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("signs in by way of the login page and comes back to this invitation", async () => {
    answering(SIGNED_OUT);
    const { result } = renderHook(() => useInvitationAccept(TOKEN), { wrapper });
    await waitFor(() => expect(result.current.view.kind).toBe("signIn"));

    act(() => result.current.onSignIn());

    const target = new URL(mocks.push.mock.calls[0][0], "https://astro.example");
    expect(target.pathname).toBe("/auth/login");
    expect(signInReturnPath(target.searchParams.get("next"))).toBe(`/auth/invitation/${TOKEN}`);
  });

  it("accepts once signed in as the invitee", async () => {
    answering(AS_INVITEE);
    mocks.mutate.mockResolvedValue({ data: { acceptInvitation: { ok: true, errors: [] } } });
    const { result } = renderHook(() => useInvitationAccept(TOKEN), { wrapper });
    await waitFor(() => expect(result.current.view.kind).toBe("accept"));

    await act(() => result.current.onAccept());

    expect(mocks.mutate).toHaveBeenCalledWith({ variables: { input: { token: TOKEN } } });
    expect(result.current.view).toEqual({ kind: "joined", invitation: INVITATION });
  });

  it("reads the preview again after a failed accept, and shows where things stand", async () => {
    const fetch = answering(AS_INVITEE, { ...AS_INVITEE, status: "expired" });
    mocks.mutate.mockResolvedValue({
      data: {
        acceptInvitation: {
          ok: false,
          errors: [{ code: "PRECONDITION", message: "invitation has expired" }],
        },
      },
    });
    const { result } = renderHook(() => useInvitationAccept(TOKEN), { wrapper });
    await waitFor(() => expect(result.current.view.kind).toBe("accept"));

    await act(() => result.current.onAccept());

    await waitFor(() => expect(result.current.view.kind).toBe("expired"));
    expect(urls(fetch)).toEqual(["/app/auth1/invitation-preview", "/app/auth1/invitation-preview"]);
  });

  it("keeps the server's reason when the invitation is still open", async () => {
    answering(AS_INVITEE);
    mocks.mutate.mockResolvedValue({
      data: {
        acceptInvitation: {
          ok: false,
          errors: [{ code: "PRECONDITION", message: "you are already a member of this scope" }],
        },
      },
    });
    const { result } = renderHook(() => useInvitationAccept(TOKEN), { wrapper });
    await waitFor(() => expect(result.current.view.kind).toBe("accept"));

    await act(() => result.current.onAccept());

    await waitFor(() =>
      expect(result.current.view).toMatchObject({
        errorMessage: "you are already a member of this scope",
      })
    );
  });

  it("says when the browser is signed in as someone else", async () => {
    answering({
      ...SIGNED_OUT,
      viewer: { signedIn: true, email: "other@elsewhere.example", isInvitee: false },
    });
    const { result } = renderHook(() => useInvitationAccept(TOKEN), { wrapper });

    await waitFor(() => expect(result.current.view.kind).toBe("wrongAccount"));
    expect(result.current.view).toMatchObject({ viewerEmail: "other@elsewhere.example" });
  });

  it("offers a retry when the preview cannot be read", async () => {
    const fetch = answering(429, SIGNED_OUT);
    const { result } = renderHook(() => useInvitationAccept(TOKEN), { wrapper });
    await waitFor(() => expect(result.current.view.kind).toBe("unavailable"));

    act(() => result.current.onRetry());

    await waitFor(() => expect(result.current.view.kind).toBe("signIn"));
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe("what the preview means", () => {
  const accept = { accepting: false, errorMessage: null };
  it.each([
    [{ status: "not_found", viewer: { signedIn: false } }, "notFound"],
    [{ ...SIGNED_OUT, status: "revoked" }, "revoked"],
    [{ ...SIGNED_OUT, status: "expired" }, "expired"],
    [{ ...SIGNED_OUT, status: "accepted" }, "used"],
    [{ ...AS_INVITEE, status: "accepted" }, "joined"],
    [
      {
        ...AS_INVITEE,
        viewer: { signedIn: true, email: "x@y.example", isInvitee: false },
        status: "accepted",
      },
      "used",
    ],
    [{ status: "pending", viewer: { signedIn: false } }, "unavailable"],
  ] as [InvitationPreview, string][])("%o is %s", (preview, kind) => {
    expect(invitationView(preview, accept).kind).toBe(kind);
  });
});
