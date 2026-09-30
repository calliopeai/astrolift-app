import { readFileSync } from "node:fs";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import {
  emptyContainer,
  emptyModel,
  emptyService,
  emptyWorkload,
  tomlToModel,
} from "@/lib/manifest/model";
import {
  validateManifest,
  WORKLOAD_RESOURCE_FIELDS,
  CONTAINER_FIELDS,
  KIND_TUNING,
} from "@/lib/manifest/schema";
import { validateAgentConfig } from "@/lib/manifest/agent-config-schema";
import { agentModelToToml, tomlToAgentModel } from "@/lib/manifest/agent-config-model";
import { APP, DRAFT } from "./app-config-manifest.fixtures";
import { AGENT_MODEL, PREVIEW_PROPS, RENDER_ERROR } from "./app-config-agent.fixtures";
import { ManifestFormPane } from "./ManifestFormPane";
import { AgentConfigFormPane } from "./AgentConfigFormPane";
import { ManifestPreviewScreen } from "./ManifestPreviewScreen";
import { localizeConfigError } from "./config-copy";
import { useConfigEditor } from "./use-config-editor";
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), message: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/apps/checkout/config",
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("@/components/PageShell", () => ({
  PageShell: ({ title, children }: { title: ReactNode; children: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {children}
    </div>
  ),
}));
const locales = ["en", "es", "fr", "de", "pt-BR", "ja", "ko", "zh-Hans"];
const catalog = (locale: string) => JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
beforeEach(() => vi.clearAllMocks());
it.each(locales)(
  "%s localizes all current schema field labels and actual validation without changing diagnostics",
  (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.config" });
    for (const field of [
      ...WORKLOAD_RESOURCE_FIELDS,
      ...CONTAINER_FIELDS,
      ...Object.values(KIND_TUNING).flat(),
    ])
      expect(t.has(`fields.${field.key}`), field.key).toBe(true);
    const model = emptyModel();
    const container = emptyContainer("");
    container.healthcheck.kind = "invalid";
    model.workloads = [
      {
        ...emptyWorkload(""),
        kind: "cronjob",
        concurrency_policy: "invalid",
        containers: [container],
      },
      { ...emptyWorkload("duplicate/raw"), kind: "workflow" },
      {
        ...emptyWorkload("duplicate/raw"),
        kind: "static_site",
        extra: { static_build_command: "npm run build" },
      },
      { ...emptyWorkload("image-fn"), kind: "faas", extra: { faas_runtime: "python3.12" } },
      { ...emptyWorkload("zip-fn"), kind: "faas", extra: { faas_package_type: "zip" } },
      { ...emptyWorkload("unknown"), kind: "invalid" },
    ];
    model.managedServices = [emptyService("")];
    const errors = validateManifest(model);
    const original = structuredClone(errors);
    const translated = errors.map((error) => localizeConfigError(error, t));
    expect(translated.map((e) => e.path)).toEqual(original.map((e) => e.path));
    expect(errors).toEqual(original);
    if (locale !== "en")
      for (const [i, error] of errors.entries())
        expect(translated[i].message).not.toBe(error.message);
    expect(translated.find((e) => e.message.includes("duplicate/raw"))?.message).toBe(
      t("validation.duplicateWorkload", { name: "duplicate/raw" })
    );
    const agent = structuredClone(AGENT_MODEL);
    agent.astrolift_version = "";
    agent.skills = [
      { ...agent.skills[0], slug: "" },
      { ...agent.skills[0], slug: "same/raw" },
      { ...agent.skills[0], slug: "same/raw" },
    ];
    agent.tools = [
      {
        ...agent.tools[0],
        slug: "",
        input_schema: "[]",
        output_schema: "{}",
        implementation_config: "{}",
      },
      {
        ...agent.tools[0],
        slug: "same/raw",
        input_schema: "{}",
        output_schema: "{}",
        implementation_config: "{}",
      },
      {
        ...agent.tools[0],
        slug: "same/raw",
        input_schema: "{}",
        output_schema: "{}",
        implementation_config: "{}",
      },
    ];
    agent.environment.vars = [{ key: "allow_install", value: "true" }];
    agent.tools[1].input_schema = '{"raw-key": null}';
    agent.tools[2].output_schema = "{invalid JSON";
    const agentErrors = validateAgentConfig(agent);
    const localized = agentErrors.map((error) => localizeConfigError(error, t));
    if (locale !== "en")
      for (const [i, error] of agentErrors.entries())
        expect(localized[i].message).not.toBe(error.message);
    expect(localized.find((e) => e.path === "tools[0].input_schema")?.message).toBe(
      t("validation.jsonObject", { field: t("builder.inputSchema") })
    );
    expect(localized.find((e) => e.path === "tools[1].input_schema")?.message).toBe(
      t("validation.notRepresentable", { field: t("builder.inputSchema") })
    );
    const rawDiagnostic = agentErrors
      .find((e) => e.path === "tools[2].output_schema")!
      .message.replace(/^[^:]*: /, "");
    expect(localized.find((e) => e.path === "tools[2].output_schema")?.message).toContain(
      rawDiagnostic
    );
    const unknown = { path: "new.field", message: "opaque parser diagnostic: <unknown-key>" };
    expect(localizeConfigError(unknown, t)).toBe(unknown);
  }
);
it.each(locales)(
  "%s edits manifest and agent forms without translating serialized keys or values",
  (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.config" });
    const changed = vi.fn();
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <ManifestFormPane draft={DRAFT} onDraftChange={changed} onSwitchToCode={vi.fn()} />
      </NextIntlClientProvider>
    );
    fireEvent.change(screen.getByRole("textbox", { name: t("builder.appName") }), {
      target: { value: "raw-app-identity" },
    });
    const parsed = tomlToModel(changed.mock.calls.at(-1)![0]);
    expect(parsed.safe).toBe(true);
    expect(parsed.model.name).toBe("raw-app-identity");
    expect(parsed.model.workloads[0].containers[0].image_ref).toBe("ghcr.io/acme/checkout:1.4.2");
    expect(screen.getAllByRole("button", { name: t("builder.collapse") }).length).toBeGreaterThan(
      0
    );
    view.unmount();
    const changedAgent = vi.fn();
    const agentView = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <AgentConfigFormPane
          draft={agentModelToToml(AGENT_MODEL)}
          onDraftChange={changedAgent}
          onSwitchToCode={vi.fn()}
        />
      </NextIntlClientProvider>
    );
    fireEvent.change(screen.getByRole("textbox", { name: "astrolift_version" }), {
      target: { value: "raw-version" },
    });
    const parsedAgent = tomlToAgentModel(changedAgent.mock.calls.at(-1)![0]);
    expect(parsedAgent.safe).toBe(true);
    expect(parsedAgent.model.astrolift_version).toBe("raw-version");
    expect(parsedAgent.model.tools[0].handler_ref).toBe(AGENT_MODEL.tools[0].handler_ref);
    agentView.unmount();
  }
);
it.each(locales)(
  "%s previews real resource identities and raw backend render failures",
  (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.config.preview" });
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <ManifestPreviewScreen {...PREVIEW_PROPS} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("heading", { name: t("title") })).toBeVisible();
    expect(
      screen.getByText(t("resourceCount", { count: PREVIEW_PROPS.result!.resources.length }))
    ).toBeVisible();
    expect(screen.getByLabelText(t("imageTag"))).toBeVisible();
    view.rerender(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <ManifestPreviewScreen {...PREVIEW_PROPS} result={RENDER_ERROR} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(RENDER_ERROR.error!)).toBeVisible();
    expect(screen.getByText(RENDER_ERROR.errorPath!)).toBeVisible();
    expect(screen.getByText(t("line", { line: RENDER_ERROR.errorLine! }))).toBeVisible();
    view.unmount();
  }
);
it.each(locales)(
  "%s localizes save and push results while preserving actual manifest and server errors",
  async (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.config.toasts" });
    const requests: { name: string; variables: Record<string, unknown> }[] = [];
    let raw = DRAFT;
    let denied = false;
    const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: new ApolloLink(
        (operation) =>
          new Observable((observer) => {
            requests.push({ name: operation.operationName ?? "", variables: operation.variables });
            queueMicrotask(() => {
              let data: Record<string, unknown>;
              if (operation.operationName === "GetApp")
                data = { astroliftApp: { ...APP, rawManifest: raw } };
              else if (operation.operationName === "GetRenderedManifest")
                data = { astroliftRenderedManifest: null };
              else if (operation.operationName === "UpdateManifest") {
                if (!denied) raw = operation.variables.input.rawManifest;
                data = {
                  updateManifest: {
                    ok: !denied,
                    errors: denied
                      ? [{ code: "PERMISSION_DENIED", message: "Owner grant required" }]
                      : [],
                    data: denied
                      ? null
                      : {
                          id: APP.id,
                          syncState: "db_ahead",
                          rawManifest: raw,
                          rawManifestStaged: "",
                        },
                  },
                };
              } else
                data = {
                  pushManifestToRepo: {
                    ok: true,
                    errors: [],
                    data: {
                      id: APP.id,
                      prUrl: "https://source.example/pr/1",
                      branchName: "config/raw-branch",
                      note: "",
                    },
                  },
                };
              observer.next({ data });
              observer.complete();
            });
          })
      ),
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ApolloProvider client={client}>
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          {children}
        </NextIntlClientProvider>
      </ApolloProvider>
    );
    const hook = renderHook(() => useConfigEditor("checkout"), { wrapper });
    await waitFor(() => expect(hook.result.current.draft).toBe(DRAFT));
    const draft = DRAFT.replace('name = "checkout"', 'name = "raw-app"');
    act(() => hook.result.current.setDraft(draft));
    await act(async () => {
      await hook.result.current.handleSave();
    });
    expect(requests.find((r) => r.name === "UpdateManifest")?.variables).toEqual({
      input: { id: APP.id, rawManifest: draft },
    });
    expect(toasts.success).toHaveBeenCalledWith(t("saved"));
    await act(async () => {
      await hook.result.current.handlePush();
    });
    expect(toasts.success).toHaveBeenCalledWith(t("prOpened", { branch: "config/raw-branch" }));
    denied = true;
    act(() => hook.result.current.setDraft(DRAFT));
    await act(async () => {
      await hook.result.current.handleSave();
    });
    expect(toasts.error).toHaveBeenLastCalledWith("Owner grant required");
    hook.unmount();
    client.stop();
  }
);
