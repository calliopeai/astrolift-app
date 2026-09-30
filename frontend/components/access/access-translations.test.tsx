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

import { GrantSource } from "./GrantSource";
import { PrincipalChip } from "./PrincipalChip";
import type { PrincipalKind } from "./access-model";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const inherited = {
  en: "inherited from project checkout",
  es: "heredado de proyecto checkout",
  fr: "hérité de projet checkout",
  de: "geerbt von Projekt checkout",
  ja: "プロジェクト checkout から継承",
  ko: "프로젝트 checkout에서 상속",
  "zh-Hans": "继承自项目 checkout",
  "pt-BR": "herdado de projeto checkout",
};

describe.each(Object.entries(catalogs))("%s access presentation", (locale, messages) => {
  it("translates the inherited phrase and retains its actual edit location", () => {
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <GrantSource
          source={{
            inheritedFrom: {
              kind: "PROJECT",
              id: "project-1",
              name: "checkout",
              href: "/projects/checkout",
            },
          }}
        />
      </NextIntlClientProvider>
    );
    const link = screen.getByRole("link", { name: inherited[locale as keyof typeof inherited] });
    expect(link).toHaveAttribute("href", "/projects/checkout");
    expect(link.parentElement).toHaveAttribute(
      "title",
      inherited[locale as keyof typeof inherited]
    );
    expect(link.parentElement).toHaveAttribute("data-source", "inherited");
    expect(onError).not.toHaveBeenCalled();
  });

  it("preserves group identifiers and both independent source links", () => {
    const group = "idp:<admins>{name}";
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <GrantSource
          source={{
            via: { kind: "group", group, href: "/administration/access/groups/actual" },
            inheritedFrom: {
              kind: "ORG",
              id: "org-1",
              name: "acme",
              href: "/administration/access",
            },
          }}
        />
      </NextIntlClientProvider>
    );
    const links = screen.getAllByRole("link");
    expect(links[0]).toHaveTextContent(group);
    expect(links[0]).toHaveAttribute("href", "/administration/access/groups/actual");
    expect(links[1]).toHaveAttribute("href", "/administration/access");
    expect(links[0].parentElement).toHaveAttribute("data-source", "group inherited");
    expect(links[0].parentElement?.title).toContain(group);
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(["user", "group", "team", "token"] as PrincipalKind[])(
    "removes only the selected %s through its original callback",
    (kind) => {
      const onRemove = vi.fn();
      const onError = vi.fn();
      const name = "Actual <principal> {kind}";
      const t = createTranslator({ locale, messages, namespace: "shared.access" });
      render(
        <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
          <PrincipalChip
            principal={{
              kind,
              id: "stable-17",
              name,
              href: "/access/actual",
              detail: "Original provider detail",
            }}
            variant="block"
            onRemove={onRemove}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("link")).toHaveAttribute("href", "/access/actual");
      expect(screen.getByText("stable-17")).toBeVisible();
      expect(screen.getByText("Original provider detail")).toBeVisible();
      fireEvent.click(
        screen.getByRole("button", { name: t("remove", { kind: t(`principal.${kind}`), name }) })
      );
      expect(onRemove).toHaveBeenCalledTimes(1);
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
