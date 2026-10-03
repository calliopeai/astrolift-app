import { readFileSync } from "node:fs";
import { MockedProvider } from "@apollo/client/testing/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { useLocalListState } from "@/components/list/use-list-state";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { CREATE_PIPELINE, SET_PIPELINE_SECRET } from "@/graphql/pipelines/pipelines.mutations";
import { LIST_PIPELINE_SECRETS } from "@/graphql/pipelines/pipelines.queries";
import { NewPipelineScreen } from "./NewPipelineScreen";
import { PipelineDetailScreen, RunGraphView } from "./PipelineDetail";
import { PipelineSecretsView } from "./PipelineSecrets";
import { PipelineListView, RunHistoryView } from "./PipelinesScreen";
import { PIPELINES_LIST, PIPELINE_RUNS_LIST, PIPELINE_SECRETS_LIST } from "./pipelines-list";
import {
  DETAIL,
  PIPELINES,
  SECRETS,
  pipelineListProps,
  runHistoryProps,
  secretsProps,
} from "./pipelines-previews.fixtures";
import { useNewPipeline } from "./use-new-pipeline";
import { usePipelineSecrets } from "./use-pipeline-secrets";

const feedback = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  push: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: feedback }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: feedback.push, replace: vi.fn() }),
  usePathname: () => "/pipelines",
  useSearchParams: () => new URLSearchParams(),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
function copy(locale: string) {
  return createTranslator({ locale, messages: catalogs[locale], namespace: "PipelineUI" });
}
function Provider({
  locale,
  errors,
  children,
}: {
  locale: string;
  errors: (error: unknown) => void;
  children: ReactNode;
}) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      onError={errors}
    >
      <PermissionsProvider
        value={{
          granted: new Set(["app.update", "pipeline.secret_manage", "secret.write"]),
          loading: false,
        }}
      >
        <MockedProvider>{children}</MockedProvider>
      </PermissionsProvider>
    </NextIntlClientProvider>
  );
}
function wrap(locale: string, errors = vi.fn()) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <Provider locale={locale} errors={errors}>
        {children}
      </Provider>
    );
  };
}
function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, entry]) =>
      typeof entry === "string"
        ? [[prefix + key, entry]]
        : Object.entries(flatten(entry as Record<string, unknown>, prefix + key + "."))
    )
  );
}
function argumentsOf(nodes: MessageFormatElement[]): string[] {
  return nodes
    .flatMap((node): string[] => {
      const own = node.type === 0 || node.type === 7 ? [] : [`${node.type}:${node.value}`];
      if ("options" in node)
        return [
          ...own,
          ...Object.values(node.options).flatMap((option) => argumentsOf(option.value)),
        ];
      if ("children" in node) return [...own, ...argumentsOf(node.children)];
      return own;
    })
    .sort();
}
function List({ empty = false }: { empty?: boolean }) {
  const list = useLocalListState(PIPELINES_LIST);
  return (
    <PipelineListView
      {...pipelineListProps(empty ? { rows: [], totalCount: 0 } : {})}
      list={list}
    />
  );
}
function Runs({ loading = false }: { loading?: boolean }) {
  const list = useLocalListState(PIPELINE_RUNS_LIST);
  return (
    <RunHistoryView
      {...runHistoryProps(
        loading ? { rows: [], options: [], selected: null, loadingOptions: true } : {}
      )}
      list={list}
    />
  );
}
function Secrets({
  save,
  remove,
}: {
  save: (name: string, value: string) => Promise<boolean>;
  remove?: ReturnType<typeof secretsProps>["deleteSecret"];
}) {
  const list = useLocalListState(PIPELINE_SECRETS_LIST);
  return (
    <PipelineSecretsView
      {...secretsProps({ saveSecret: save, ...(remove ? { deleteSecret: remove } : {}) })}
      list={list}
    />
  );
}
function Create() {
  return <NewPipelineScreen {...useNewPipeline()} />;
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe.each(locales)("pipeline product copy (%s)", (locale) => {
  it("keeps catalogue keys and ICU arguments/tags compatible", () => {
    for (const namespace of ["PipelineUI", "graph"]) {
      const en = flatten(
        namespace === "graph" ? catalogs.en.shared.pipelineGraph : catalogs.en.PipelineUI
      );
      const translated = flatten(
        namespace === "graph" ? catalogs[locale].shared.pipelineGraph : catalogs[locale].PipelineUI
      );
      expect(Object.keys(translated).sort()).toEqual(Object.keys(en).sort());
      for (const key of Object.keys(en)) {
        expect(translated[key].trim(), key).not.toBe("");
        expect(argumentsOf(parse(translated[key])), key).toEqual(argumentsOf(parse(en[key])));
      }
    }
  });
  it("renders list/empty labels while retaining repository identities and destinations", () => {
    const errors = vi.fn(),
      t = copy(locale),
      view = render(<List />, { wrapper: wrap(locale, errors) });
    expect(screen.getByRole("columnheader", { name: t("repository") })).toBeVisible();
    expect(screen.getByPlaceholderText(t("list.pipelinesSearch"))).toBeVisible();
    expect(screen.getByText(PIPELINES[0].name)).toBeVisible();
    expect(
      screen.getByRole("link", { name: PIPELINES[0].repoUrl.replace(/^https?:\/\//, "") })
    ).toHaveAttribute("href", PIPELINES[0].repoUrl);
    view.rerender(<List empty />);
    expect(screen.getByText(t("noPipelines"))).toBeVisible();
    expect(screen.getByRole("link", { name: t("newPipeline") })).toHaveAttribute(
      "href",
      "/pipelines/new"
    );
    expect(errors).not.toHaveBeenCalled();
  });
  it("renders the real run picker loading state and keeps unknown server status/trigger values", () => {
    const errors = vi.fn(),
      t = copy(locale),
      view = render(<Runs loading />, { wrapper: wrap(locale, errors) });
    expect(screen.getByRole("combobox", { name: t("pipeline") })).toHaveTextContent(
      t("loadingPipelines")
    );
    view.unmount();
    render(
      <PipelineDetailScreen
        {...DETAIL}
        runs={[
          {
            ...DETAIL.runs[0],
            status: "FUTURE_STATUS_v2",
            triggerKind: "CUSTOM_TRIGGER_v2",
            triggerRef: "refs/heads/literal-branch",
          },
        ]}
        secrets={null}
      />,
      { wrapper: wrap(locale, errors) }
    );
    expect(screen.getByText("FUTURE_STATUS_v2")).toBeVisible();
    expect(screen.getByText("CUSTOM_TRIGGER_v2")).toBeVisible();
    expect(screen.getByText("literal-branch")).toBeVisible();
    expect(errors).not.toHaveBeenCalled();
  });
  it("localizes errors and Retry while retaining raw diagnostics and retry callbacks", () => {
    const t = copy(locale),
      errors = vi.fn(),
      retry = vi.fn();
    render(
      <PipelineDetailScreen
        {...DETAIL}
        pipeline={null}
        pipelineError={new Error("RAW_SERVER_REFUSAL")}
        onRetryPipeline={retry}
        runs={[]}
        runsError={new Error("RAW_LOG_FAILURE")}
        secrets={null}
      />,
      { wrapper: wrap(locale, errors) }
    );
    expect(screen.getByText(t("detail.loadFailed"))).toBeVisible();
    expect(screen.getByText(t("detail.runsFailed"))).toBeVisible();
    expect(screen.getByText("RAW_SERVER_REFUSAL")).toBeVisible();
    expect(screen.getByText("RAW_LOG_FAILURE")).toBeVisible();
    fireEvent.click(screen.getAllByRole("button", { name: t("retry") })[0]);
    expect(retry).toHaveBeenCalledOnce();
    expect(errors).not.toHaveBeenCalled();
  });
  it("renders truthful unavailable tabs and graph loading/empty states", () => {
    const t = copy(locale),
      errors = vi.fn(),
      view = render(<PipelineDetailScreen {...DETAIL} tab="logs" secrets={null} />, {
        wrapper: wrap(locale, errors),
      });
    for (const tab of ["logs", "artifacts", "triggers", "runners"] as const) {
      view.rerender(<PipelineDetailScreen {...DETAIL} tab={tab} secrets={null} />);
      expect(screen.getByText(t(`detail.${tab}Title`))).toBeVisible();
      expect(screen.getByText(t(`detail.${tab}Description`))).toBeVisible();
    }
    view.rerender(<RunGraphView loading stages={[]} />);
    expect(screen.getByLabelText(t("detail.graphLoading"))).toBeVisible();
    view.rerender(<RunGraphView loading={false} stages={[]} />);
    expect(screen.getByText(t("detail.graphEmpty"))).toBeVisible();
    expect(errors).not.toHaveBeenCalled();
  });
  it("preserves password drafts and literal TOML references across locale changes and refusals", async () => {
    const user = userEvent.setup(),
      errors = vi.fn(),
      save = vi.fn().mockResolvedValue(false),
      t = copy(locale);
    const view = render(
      <Provider locale={locale} errors={errors}>
        <Secrets save={save} />
      </Provider>
    );
    expect(screen.getByText("${secrets.NAME}")).toBeVisible();
    await user.click(screen.getByRole("button", { name: t("secrets.add") }));
    await user.type(screen.getByLabelText(t("name")), "literal_key");
    await user.type(screen.getByLabelText(t("secrets.value")), "controlled-draft");
    const next = locale === "ja" ? "fr" : "ja",
      translated = copy(next);
    view.rerender(
      <Provider locale={next} errors={errors}>
        <Secrets save={save} />
      </Provider>
    );
    expect(screen.getByLabelText(translated("name"))).toHaveValue("LITERAL_KEY");
    expect(screen.getByLabelText(translated("secrets.value"))).toHaveAttribute("type", "password");
    expect(screen.getByLabelText(translated("secrets.value"))).toHaveValue("controlled-draft");
    await user.click(screen.getByRole("button", { name: translated("secrets.save") }));
    await waitFor(() => expect(save).toHaveBeenCalledWith("LITERAL_KEY", "controlled-draft"));
    expect(screen.getByLabelText(translated("secrets.value"))).toHaveValue("controlled-draft");
    expect(errors).not.toHaveBeenCalled();
  });
  it("reviews the exact secret deletion in the selected locale and retains its target on refusal", async () => {
    const user = userEvent.setup(),
      t = copy(locale),
      errors = vi.fn();
    const listCopy = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "shared.list",
    });
    const confirmationCopy = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "shared.confirmation",
    });
    let finish!: () => void;
    const remove = vi
      .fn()
      .mockRejectedValueOnce(new Error("RAW_DELETE_REFUSAL"))
      .mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            finish = resolve;
          })
      );
    render(<Secrets save={vi.fn()} remove={remove} />, { wrapper: wrap(locale, errors) });
    await user.click(
      screen.getAllByRole("button", {
        name: listCopy("rowActions", { label: t("tabs.secrets") }),
      })[0]
    );
    await user.click(
      screen.getByRole("menuitem", { name: t("secrets.deleteNamed", { name: SECRETS[0].name }) })
    );
    const dialog = screen.getByRole("alertdialog");
    expect(
      within(dialog).getByText(t("secrets.deleteTitle", { name: SECRETS[0].name }))
    ).toBeVisible();
    expect(within(dialog).getByText(t("secrets.deleteDescription"))).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: t("secrets.delete") }));
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith("RAW_DELETE_REFUSAL"));
    expect(dialog).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: t("secrets.delete") }));
    expect(
      within(dialog).getByRole("button", { name: confirmationCopy("working") })
    ).toBeDisabled();
    expect(remove.mock.calls).toEqual([[SECRETS[0]], [SECRETS[0]]]);
    await act(async () => finish());
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(errors).not.toHaveBeenCalled();
  });
  it("retains exact creation variables on refusal and translates fallback/committed feedback", async () => {
    const t = copy(locale),
      errors = vi.fn();
    const input = {
      name: "literal-pipeline",
      repoUrl: "git@github.com:literal/repo.git",
      defaultBranch: "main",
      tomlPath: "ci/literal.toml",
    };
    const request = { query: CREATE_PIPELINE, variables: { input } };
    feedback.push.mockImplementationOnce(() => {
      throw new Error("controlled navigation failure");
    });
    render(
      <MockedProvider
        mocks={[
          {
            request,
            result: {
              data: {
                createPipeline: {
                  ok: false,
                  errors: [{ message: "RAW_CREATE_REFUSAL" }],
                  data: null,
                },
              },
            },
          },
          { request, result: { data: { createPipeline: { ok: false, errors: [], data: null } } } },
          {
            request,
            result: {
              data: { createPipeline: { ok: true, errors: [], data: { id: "literal-guid" } } },
            },
          },
        ]}
      >
        <Create />
      </MockedProvider>,
      { wrapper: wrap(locale, errors) }
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(t("name")), input.name);
    await user.type(screen.getByLabelText(t("repositoryUrl")), input.repoUrl);
    await user.type(screen.getByLabelText(t("tomlPath")), input.tomlPath);
    await user.click(screen.getByRole("button", { name: t("new.create") }));
    expect(await screen.findByRole("alert")).toHaveTextContent("RAW_CREATE_REFUSAL");
    expect(screen.getByLabelText(t("tomlPath"))).toHaveValue(input.tomlPath);
    await user.click(screen.getByRole("button", { name: t("new.create") }));
    expect(await screen.findByRole("alert")).toHaveTextContent(t("new.failed"));
    await user.click(screen.getByRole("button", { name: t("new.create") }));
    expect(await screen.findByRole("link", { name: t("new.open") })).toHaveAttribute(
      "href",
      "/pipelines/literal-guid"
    );
    expect(feedback.success).toHaveBeenCalledWith(t("new.createdToast"));
    expect(feedback.warning).toHaveBeenCalledWith(t("new.navigationFailed"));
    expect(errors).not.toHaveBeenCalled();
  });
  it("translates secret fallback and committed-refresh feedback while retaining server refusals", async () => {
    const t = copy(locale),
      errors = vi.fn(),
      pipelineId = "literal-guid";
    const request = {
      query: SET_PIPELINE_SECRET,
      variables: { input: { pipelineId, name: "LITERAL_KEY", value: "controlled-draft" } },
    };
    const list = { query: LIST_PIPELINE_SECRETS, variables: { pipelineId } };
    const Wrapper = wrap(locale, errors);
    const hook = renderHook(() => usePipelineSecrets(pipelineId), {
      wrapper: ({ children }) => (
        <Wrapper>
          <MockedProvider
            mocks={[
              { request: list, result: { data: { astroliftPipelineSecrets: [] } } },
              {
                request,
                result: { data: { setPipelineSecret: { ok: false, errors: [], data: null } } },
              },
              {
                request,
                result: {
                  data: {
                    setPipelineSecret: {
                      ok: false,
                      errors: [
                        { code: "PERMISSION_DENIED", message: "RAW_SECRET_REFUSAL", field: null },
                      ],
                      data: null,
                    },
                  },
                },
              },
              {
                request,
                result: {
                  data: {
                    setPipelineSecret: {
                      ok: true,
                      errors: [],
                      data: { pipelineId, name: "LITERAL_KEY" },
                    },
                  },
                },
              },
              { request: list, error: new Error("RAW_REFRESH_ERROR") },
            ]}
          >
            {children}
          </MockedProvider>
        </Wrapper>
      ),
    });
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    await act(async () => {
      expect(await hook.result.current.saveSecret("LITERAL_KEY", "controlled-draft")).toBe(false);
    });
    expect(feedback.error).toHaveBeenCalledWith(t("secrets.saveFailed"));
    await act(async () => {
      expect(await hook.result.current.saveSecret("LITERAL_KEY", "controlled-draft")).toBe(false);
    });
    expect(feedback.error).toHaveBeenCalledWith("RAW_SECRET_REFUSAL");
    await act(async () => {
      expect(await hook.result.current.saveSecret("LITERAL_KEY", "controlled-draft")).toBe(true);
    });
    expect(feedback.success).toHaveBeenCalledWith(t("secrets.saved", { name: "LITERAL_KEY" }));
    expect(feedback.warning).toHaveBeenCalledWith(t("secrets.refreshFailed"));
    expect(errors).not.toHaveBeenCalled();
  });
});
