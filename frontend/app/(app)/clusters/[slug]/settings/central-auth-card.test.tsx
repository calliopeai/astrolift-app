import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { CentralAuthCard, IngressClassCard } from "./central-auth-card";

/**
 * Central auth and the ingress class, from the UI (#2119). Secrets are
 * write-only: the save never sends one the operator did not just type, so
 * the server keeps the stored value.
 */

const calls = vi.hoisted(() => ({ variables: [] as unknown[] }));

vi.mock("@apollo/client/react", () => ({
  useMutation: () => [
    vi.fn(async (opts: { variables: unknown }) => {
      calls.variables.push(opts.variables);
      return { data: { updateTenantCluster: { ok: true, errors: [], data: null } } };
    }),
    { loading: false },
  ],
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function cluster(overrides: Record<string, unknown> = {}): AstroliftTenantCluster {
  return {
    id: "c1",
    slug: "edge",
    ingressClass: "alb",
    albAuthConfig: { user_pool_arn: "arn", user_pool_client_id: "x", user_pool_domain: "d" },
    oidcAuthConfig: {
      discovery_url: "https://issuer.example.net/.well-known/openid-configuration",
      client_id: "central",
      auth_proxy_host: "auth.apps.example.net",
      logout_url: "https://issuer.example.net/logout",
      client_secret_set: true,
      cookie_secret_set: false,
      gateway_secret_set: false,
    },
    ...overrides,
  } as unknown as AstroliftTenantCluster;
}

beforeEach(() => {
  calls.variables = [];
});

describe("CentralAuthCard (#2119)", () => {
  it("shows whether each secret is set, never a value", () => {
    render(<CentralAuthCard cluster={cluster()} />);

    expect(screen.getByText("Client secret: set")).toBeInTheDocument();
    expect(screen.getByText("Cookie secret: not set")).toBeInTheDocument();
    expect(screen.getByText("auth.apps.example.net")).toBeInTheDocument();
  });

  it("an edit that leaves the secret blank sends no secret, and keeps unedited keys", async () => {
    render(<CentralAuthCard cluster={cluster()} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Auth host"), {
      target: { value: "auth.new.example.net" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(calls.variables).toHaveLength(1));
    const sent = (calls.variables[0] as { input: { oidcAuthConfig: Record<string, string> } }).input
      .oidcAuthConfig;
    expect(sent.auth_proxy_host).toBe("auth.new.example.net");
    expect(sent.logout_url).toBe("https://issuer.example.net/logout");
    expect(sent).not.toHaveProperty("client_secret");
    expect(Object.keys(sent).some((k) => k.endsWith("_set"))).toBe(false);
  });

  it("a typed secret is sent", async () => {
    render(<CentralAuthCard cluster={cluster()} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(calls.variables).toHaveLength(1));
    const sent = (calls.variables[0] as { input: { oidcAuthConfig: Record<string, string> } }).input
      .oidcAuthConfig;
    expect(sent.client_secret).toBe("s3cret");
  });
});

describe("IngressClassCard (#2119)", () => {
  it("warns before a change that would leave apps with no gate", () => {
    render(<IngressClassCard cluster={cluster({ oidcAuthConfig: null })} />);

    fireEvent.click(screen.getByRole("combobox", { name: "Ingress class" }));
    fireEvent.click(screen.getByRole("option", { name: "Envoy edge (central auth)" }));

    expect(screen.getByRole("alert").textContent).toMatch(/would be public/);
  });

  it("does not redeploy every app unless asked", async () => {
    render(<IngressClassCard cluster={cluster()} />);

    fireEvent.click(screen.getByRole("combobox", { name: "Ingress class" }));
    fireEvent.click(screen.getByRole("option", { name: "Envoy edge (central auth)" }));
    fireEvent.click(screen.getByRole("button", { name: "Change class" }));
    expect(screen.getByText(/No app redeploys now/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Change class" }).at(-1)!);

    await waitFor(() => expect(calls.variables).toHaveLength(1));
    expect(calls.variables[0]).toEqual({
      input: { id: "c1", ingressClass: "envoy", syncManifests: false },
    });
  });
});
