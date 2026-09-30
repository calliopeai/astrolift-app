import { readFileSync } from "node:fs";
import path from "node:path";
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider, createTranslator } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";
import {
  AgentSecretBundleReadDenied,
  AgentSecretSource,
  type AgentSecretSourceProps,
} from "./AgentSecretSource";

describe("localized explicit environment-spec selection", () => {
  it.each(locales)("%s preserves scope gates, search and page callbacks", (locale) => {
    const messages = JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8"));
    const copy = messages.agentSecrets.source;
    const t = createTranslator({ locale, messages, namespace: "agentSecrets.source" });
    const onError = vi.fn();
    const props: AgentSecretSourceProps = {
      access: "ready",
      options: [{ id: "actual-selected-id", slug: "actual-recipe", name: "Actual Recipe" }],
      optionsLoading: false,
      optionsError: null,
      search: "",
      onSearch: vi.fn(),
      page: 2,
      totalCount: 1000,
      pageSize: 20,
      onPage: vi.fn(),
      onRetryOptions: vi.fn(),
      selection: null,
      onSelect: vi.fn(),
      onClear: vi.fn(),
      sourceState: "unselected",
      onVerify: vi.fn(),
      canListSecrets: true,
      children: <p>fixture editor</p>,
    };
    const view = (changes: Partial<AgentSecretSourceProps> = {}) => (
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <AgentSecretSource {...props} {...changes} />
      </NextIntlClientProvider>
    );
    const { rerender } = render(view());
    expect(screen.getByRole("heading", { name: copy.title })).toBeVisible();
    expect(
      screen.getByText(t("count", { count: 1000, page: 2 }), {
        normalizer: (value) => value.trim(),
      })
    ).toBeVisible();
    expect(screen.queryByText("fixture editor")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: copy.search }), {
      target: { value: " actual search " },
    });
    expect(props.onSearch).toHaveBeenLastCalledWith(" actual search ");
    fireEvent.click(screen.getByRole("button", { name: copy.previous }));
    fireEvent.click(screen.getByRole("button", { name: copy.next }));
    expect(props.onPage).toHaveBeenNthCalledWith(1, 1);
    expect(props.onPage).toHaveBeenNthCalledWith(2, 3);

    for (const access of ["loading", "denied", "error"] as const) {
      rerender(view({ access }));
      expect(screen.getByRole("status")).toHaveTextContent(
        copy[{ loading: "accessLoading", denied: "accessDenied", error: "accessError" }[access]]
      );
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    }
    rerender(view({ optionsLoading: true }));
    expect(screen.getByRole("button", { name: copy.next })).toBeDisabled();
    expect(screen.getByRole("button", { name: copy.previous })).toBeDisabled();
    expect(screen.getByText(copy.loadingChoices)).toBeVisible();
    expect(
      screen.queryByText(t("count", { count: 1000, page: 2 }), {
        normalizer: (value) => value.trim(),
      })
    ).not.toBeInTheDocument();
    rerender(view({ optionsError: "private transport diagnostic" }));
    expect(screen.getByRole("alert")).toHaveTextContent(copy.listError);
    expect(screen.queryByText("private transport diagnostic")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.retryChoices }));
    expect(props.onRetryOptions).toHaveBeenCalledOnce();
    rerender(view({ options: [], totalCount: 0 }));
    expect(screen.getByText(copy.empty)).toBeVisible();
    const selection = props.options[0];
    rerender(view({ selection, sourceState: "confirmed", canListSecrets: false }));
    expect(screen.getByText(copy.listDenied)).toBeVisible();
    expect(screen.queryByText("fixture editor")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.verify }));
    fireEvent.click(screen.getByRole("button", { name: copy.clear }));
    expect(props.onVerify).toHaveBeenCalledOnce();
    expect(props.onClear).toHaveBeenCalledOnce();
    for (const [state, key] of [
      ["loading", "checking"],
      ["unavailable", "unavailable"],
      ["error", "verifyError"],
    ] as const) {
      rerender(view({ selection, sourceState: state }));
      expect(screen.getByText(copy[key])).toBeVisible();
      expect(screen.queryByText("fixture editor")).not.toBeInTheDocument();
    }
    rerender(view({ selection, sourceState: "confirmed" }));
    expect(screen.getByText("fixture editor")).toBeVisible();
    rerender(
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <AgentSecretBundleReadDenied />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("status")).toHaveTextContent(copy.bundlesDenied);
    expect(onError).not.toHaveBeenCalled();
  });
});
