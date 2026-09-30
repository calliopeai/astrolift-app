import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { NextIntlClientProvider } from "next-intl";
import { StrictMode, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { buildSchema, validate } from "graphql";
import schemaSDL from "@/schema.graphql?raw";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { SharedModelPromptPanel } from "./SharedModelPromptPanel";
import { SharedModelPromptClient } from "./SharedModelPromptClient";
import { sharedModelPromptProps } from "./shared-model-prompt.fixtures";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import {
  GET_SHARED_MODEL_PROMPT_READINESS,
  TEST_SHARED_MODEL_ENDPOINT,
} from "@/graphql/models/shared-prompt.queries";
import type { SharedPromptResult } from "./shared-model-prompt";
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const copy = en.models.shared.prompt;
const scope = vi.hoisted(() => ({ id: "org", loading: false, error: null as Error | null }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({
    org: scope.id ? { id: scope.id } : null,
    loading: scope.loading,
    error: scope.error,
  }),
}));
type Request = { operationName: string; variables: Record<string, unknown>; query: string };
let requests: Request[], transport: (request: Request) => Promise<Response>;
const response = (data: Record<string, unknown>) =>
  new Response(JSON.stringify({ data }), { headers: { "Content-Type": "application/json" } });
const successful = {
  ok: true,
  errors: [],
  data: {
    status: "succeeded",
    reply: "Actual model reply",
    latencyMs: 0,
    promptTokens: 4,
    completionTokens: 8,
    totalTokens: 12,
    error: "",
  },
};
function wrapper() {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: async (_uri, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        return transport(request);
      },
    }),
  });
  return function Provider({ children }: PropsWithChildren) {
    return (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
const enterAndSend = async () => {
  await waitFor(() => expect(screen.getByRole("button", { name: copy.refresh })).toBeEnabled());
  fireEvent.change(screen.getByRole("textbox", { name: copy.prompt }), {
    target: { value: "  Hello model.  " },
  });
  fireEvent.click(screen.getByRole("button", { name: copy.send }));
};
beforeEach(() => {
  scope.id = "org";
  scope.loading = false;
  scope.error = null;
  requests = [];
  transport = async (request) =>
    request.operationName === "GetSharedModelPromptReadiness"
      ? response({ astroliftSharedModelPromptReadiness: sharedModelPromptProps.readiness.data })
      : response({ testSharedModelEndpoint: successful });
});
describe("actual shared owner model prompt", () => {
  it("validates both real documents against the composed main schema", () => {
    const schema = buildSchema(schemaSDL);
    expect(validate(schema, GET_SHARED_MODEL_PROMPT_READINESS)).toEqual([]);
    expect(validate(schema, TEST_SHARED_MODEL_ENDPOINT)).toEqual([]);
  });
  it("sends exact reviewed model, cluster, provider and version with a bounded trimmed prompt", async () => {
    render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await enterAndSend();
    await screen.findByText("Actual model reply");
    expect(requests[0].variables).toEqual({
      id: "shared-model",
      expectedClusterId: "cluster-one",
      expectedProviderId: "provider-one",
      expectedVersion: 5,
    });
    const request = requests.find(
      (request) => request.operationName === "TestSharedModelEndpoint"
    )!;
    expect(request.variables).toEqual({
      input: {
        managedServiceId: "shared-model",
        expectedClusterId: "cluster-one",
        expectedProviderId: "provider-one",
        expectedVersion: 5,
        prompt: "Hello model.",
      },
    });
    expect(request.query).not.toMatch(/apiKey|credential|endpointUrl/);
    expect(screen.getByText("Latency: 0 ms")).toBeInTheDocument();
    expect(screen.getByText("Total tokens: 12")).toBeInTheDocument();
  });
  it.each(["UNKNOWN_HEARTBEAT", "STALE_HEARTBEAT", "UNSUPPORTED", "UNCONFIGURED_RELAY"] as const)(
    "refuses spend on actual %s admission",
    async (state) => {
      transport = async () =>
        response({
          astroliftSharedModelPromptReadiness: {
            ...sharedModelPromptProps.readiness.data!,
            state,
            eligible: false,
          },
        });
      render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
        wrapper: wrapper(),
      });
      await screen.findByText(copy.states[state]);
      fireEvent.change(screen.getByRole("textbox", { name: copy.prompt }), {
        target: { value: "Hello" },
      });
      expect(screen.getByRole("button", { name: copy.send })).toBeDisabled();
      expect(requests).toHaveLength(1);
    }
  );
  it("fails closed on invalid server limits or an owner read refusal", async () => {
    transport = async () =>
      response({
        astroliftSharedModelPromptReadiness: {
          ...sharedModelPromptProps.readiness.data!,
          maxOutputTokens: 1024,
        },
      });
    render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(copy.failed);
    fireEvent.change(screen.getByRole("textbox", { name: copy.prompt }), {
      target: { value: "Hello" },
    });
    expect(screen.getByRole("button", { name: copy.send })).toBeDisabled();
  });
  it("keeps owner permission failure visible and readiness retry never writes", async () => {
    transport = async () =>
      new Response(
        JSON.stringify({
          errors: [
            { message: "Owner permission required", extensions: { code: "PERMISSION_DENIED" } },
          ],
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByText("Owner permission required");
    fireEvent.click(screen.getByRole("button", { name: copy.refresh }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(
      requests.every((request) => request.operationName === "GetSharedModelPromptReadiness")
    ).toBe(true);
  });
  it("preserves complete structured version refusal and does not replay after rechecking readiness", async () => {
    transport = async (request) =>
      request.operationName === "GetSharedModelPromptReadiness"
        ? response({ astroliftSharedModelPromptReadiness: sharedModelPromptProps.readiness.data })
        : response({
            testSharedModelEndpoint: {
              ok: false,
              data: null,
              errors: [
                {
                  code: "VERSION_MISMATCH",
                  field: "expectedVersion",
                  message: "Deployment changed; refresh it",
                  currentVersion: 6,
                  requestedVersion: 5,
                  requiresAttestation: null,
                  supportedMethods: null,
                },
              ],
            },
          });
    render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await enterAndSend();
    await screen.findByText("VERSION_MISMATCH");
    expect(screen.getByText("currentVersion: 6")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.refresh }));
    await waitFor(() =>
      expect(
        requests.filter((request) => request.operationName === "GetSharedModelPromptReadiness")
      ).toHaveLength(2)
    );
    expect(
      requests.filter((request) => request.operationName === "TestSharedModelEndpoint")
    ).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: copy.result })).not.toBeInTheDocument();
  });
  it.each([
    { ok: true, errors: [], data: null },
    {
      ok: true,
      errors: [
        {
          code: "REFUSED",
          message: "Request refused",
          field: null,
          currentVersion: null,
          requestedVersion: null,
          requiresAttestation: null,
          supportedMethods: null,
        },
      ],
      data: successful.data,
    },
    { ok: true, errors: [], data: { ...successful.data, status: "timed_out", reply: "" } },
    { ok: true, errors: [], data: { ...successful.data, reply: "" } },
  ])(
    "does not show success for missing, mixed, timed-out or empty replies %j",
    async (envelope) => {
      transport = async (request) =>
        request.operationName === "GetSharedModelPromptReadiness"
          ? response({ astroliftSharedModelPromptReadiness: sharedModelPromptProps.readiness.data })
          : response({ testSharedModelEndpoint: envelope });
      render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
        wrapper: wrapper(),
      });
      await enterAndSend();
      await screen.findByRole("alert");
      expect(screen.queryByRole("heading", { name: copy.result })).not.toBeInTheDocument();
      expect(
        requests.filter((request) => request.operationName === "TestSharedModelEndpoint")
      ).toHaveLength(1);
    }
  );
  it("blocks duplicate spend while one request is pending", async () => {
    let finish: ((value: Response) => void) | undefined;
    transport = async (request) =>
      request.operationName === "GetSharedModelPromptReadiness"
        ? response({ astroliftSharedModelPromptReadiness: sharedModelPromptProps.readiness.data })
        : new Promise((resolve) => {
            finish = resolve;
          });
    render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await enterAndSend();
    await screen.findByText(copy.waiting);
    fireEvent.click(screen.getByRole("button", { name: copy.send }));
    expect(
      requests.filter((request) => request.operationName === "TestSharedModelEndpoint")
    ).toHaveLength(1);
    await act(async () => finish?.(response({ testSharedModelEndpoint: successful })));
    await screen.findByText("Actual model reply");
  });
  it("permanently ignores an old request after model-version/provider changes and A→B→A", async () => {
    let finish: ((value: Response) => void) | undefined;
    transport = async (request) =>
      request.operationName === "GetSharedModelPromptReadiness"
        ? response({ astroliftSharedModelPromptReadiness: sharedModelPromptProps.readiness.data })
        : new Promise((resolve) => {
            finish = resolve;
          });
    const { rerender } = render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await enterAndSend();
    await waitFor(() => expect(finish).toBeDefined());
    rerender(
      <SharedModelPromptClient
        model={{ ...sharedModelDetailProps.model!, version: 6, providerId: "provider-two" }}
      />
    );
    rerender(<SharedModelPromptClient model={sharedModelDetailProps.model!} />);
    expect(screen.getByRole("textbox", { name: copy.prompt })).toHaveValue("");
    await act(async () => finish?.(response({ testSharedModelEndpoint: successful })));
    expect(screen.queryByText("Actual model reply")).not.toBeInTheDocument();
    expect(screen.queryByText(copy.waiting)).not.toBeInTheDocument();
  });
  it("ignores replies after unmount and remains usable after strict-mode admission", async () => {
    let finish: ((value: SharedPromptResult) => void) | undefined;
    const onRun = vi.fn(
      () =>
        new Promise<SharedPromptResult>((resolve) => {
          finish = resolve;
        })
    );
    const { unmount } = render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <StrictMode>
          <SharedModelPromptPanel {...sharedModelPromptProps} onRun={onRun} />
        </StrictMode>
      </NextIntlClientProvider>
    );
    await enterAndSend();
    await waitFor(() => expect(onRun).toHaveBeenCalledTimes(1));
    unmount();
    await act(async () =>
      finish?.({ ok: true, reply: "Late reply", latencyMs: null, totalTokens: null })
    );
    expect(screen.queryByText("Late reply")).not.toBeInTheDocument();
  });
  it("does not read or spend under a foreign current organization", async () => {
    scope.id = "foreign-org";
    render(<SharedModelPromptClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(copy.changed);
    expect(requests).toHaveLength(0);
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "renders %s prompt controls and preserves actual callback text",
    async (locale) => {
      const onRun = vi.fn(sharedModelPromptProps.onRun),
        localized = locales[locale].models.shared.prompt;
      render(
        <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
          <SharedModelPromptPanel {...sharedModelPromptProps} onRun={onRun} />
        </NextIntlClientProvider>
      );
      fireEvent.change(screen.getByRole("textbox", { name: localized.prompt }), {
        target: { value: "  Bonjour 世界  " },
      });
      fireEvent.click(screen.getByRole("button", { name: localized.send }));
      await screen.findByText("A bounded model reply.");
      expect(onRun).toHaveBeenCalledWith("Bonjour 世界");
    }
  );
  it.each(["fr", "ja"] as const)("hydrates %s prompt labels without drift", async (locale) => {
    const tree = (
      <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
        <SharedModelPromptPanel {...sharedModelPromptProps} />
      </NextIntlClientProvider>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(tree);
    document.body.appendChild(container);
    const before = container.textContent,
      onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    await act(async () => {
      root = hydrateRoot(container, tree, { onRecoverableError });
    });
    expect(container.textContent).toBe(before);
    expect(onRecoverableError).not.toHaveBeenCalled();
    await act(async () => root?.unmount());
    container.remove();
  });
});
