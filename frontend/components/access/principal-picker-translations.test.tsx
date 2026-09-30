import { fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { PrincipalPicker } from "./PrincipalPicker";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const person = {
  kind: "user" as const,
  id: "person-42",
  name: "Actual <person> {name}",
  detail: "viewer@example.test",
  href: "/people/person-42",
};
describe.each(Object.entries(catalogs))("%s principal picker", (locale, messages) => {
  it("localizes the empty search and preserves the exact user query and selected principal", () => {
    const t = createTranslator({ locale, messages, namespace: "shared.access.picker" });
    const setQuery = vi.fn();
    const onChange = vi.fn();
    const onError = vi.fn();
    const ui = (results: (typeof person)[]) => (
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <PrincipalPicker
          label="Caller field"
          value={null}
          onChange={onChange}
          search={{ query: "", setQuery, results }}
        />
      </NextIntlClientProvider>
    );
    const { rerender } = render(ui([]));
    expect(screen.getByText(t("type"))).toBeVisible();
    const input = screen.getByRole("searchbox");
    expect(input).toHaveAttribute("placeholder", t("placeholder"));
    fireEvent.change(input, { target: { value: "  query <raw>  " } });
    expect(setQuery).toHaveBeenCalledExactlyOnceWith("  query <raw>  ");
    rerender(ui([person]));
    fireEvent.click(screen.getByRole("button", { name: t("pick", { name: person.name }) }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith(person);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();
  });

  it("preserves authoritative search errors and never shows a failed read as empty", () => {
    const t = createTranslator({ locale, messages, namespace: "shared.access.picker" });
    const onChange = vi.fn();
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <PrincipalPicker
          label="Caller field"
          value={null}
          onChange={onChange}
          search={{
            query: "unknown",
            setQuery: () => {},
            results: [],
            error: { message: "IDENTITY_SCOPE_DENIED {raw}" },
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      t("searchFailed", { message: "IDENTITY_SCOPE_DENIED {raw}" })
    );
    expect(screen.queryByText(t("none", { query: "unknown" }))).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("keeps selected identifiers and clears that selected value through the existing callback", () => {
    const t = createTranslator({ locale, messages, namespace: "shared.access.picker" });
    const onChange = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <PrincipalPicker
          label="Caller field"
          value={person}
          onChange={onChange}
          search={{ query: "", setQuery: () => {}, results: [] }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("link")).toHaveAttribute("href", person.href);
    expect(screen.getByText(person.id)).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: t("changeField", { field: "Caller field" }) })
    );
    expect(onChange).toHaveBeenCalledExactlyOnceWith(null);
  });
});
