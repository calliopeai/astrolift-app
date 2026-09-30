import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import ptBR from "@/messages/pt-BR.json";
import zhHans from "@/messages/zh-Hans.json";

import { PermissionNote, Restricted } from "./Restricted";
import { SettingsPage, SettingsSection } from "./SettingsPage";

const locales = { en, es, fr, de, "pt-BR": ptBR, ja, ko, "zh-Hans": zhHans };
const permission = "cluster.settings.manage_runtime_identity_and_access_boundaries";
const serverError = "AccessDenied: sts:AssumeRole on arn:aws:iam::123456789012:role/runtime";
const plain = (message: string, values: Record<string, string>) =>
  message.replace(/<\/?code>/g, "").replace(/\{(\w+)\}/g, (_, key) => values[key]);

const note = (text: string) =>
  screen.getByText((_, element) => element?.tagName === "P" && element.textContent === text);

function Editor({
  saving = false,
  saveLabel,
  onSave,
  onCancel,
  error,
}: {
  saving?: boolean;
  saveLabel?: string;
  onSave: () => void;
  onCancel: () => void;
  error?: string;
}) {
  const [value, setValue] = React.useState("https://auth.example.com/realms/acme");
  return (
    <SettingsSection
      title="Issuer URL"
      dirty
      saving={saving}
      saveLabel={saveLabel}
      onSave={onSave}
      onCancel={onCancel}
      error={error}
    >
      <input aria-label="Issuer URL" value={value} onChange={(e) => setValue(e.target.value)} />
    </SettingsSection>
  );
}

describe.each(Object.entries(locales))("shared settings in %s", (locale, messages) => {
  const settings = messages.shared.settings;
  const restricted = messages.shared.restricted;
  const provider = (children: React.ReactNode) => (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );

  it("renders localized nav, danger and read-only copy without changing the permission or allowing save", () => {
    const save = vi.fn();
    const cancel = vi.fn();
    const { container } = render(
      provider(
        <SettingsPage
          sections={[
            {
              id: "issuer",
              title: "Issuer URL",
              content: <Editor onSave={save} onCancel={cancel} />,
            },
          ]}
          dangerZone={<button>Caller-owned destructive action</button>}
          readOnly={{ permission }}
          restrictedMode="show"
        />
      )
    );
    expect(screen.getByRole("navigation", { name: settings.sections })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: settings.section })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: settings.dangerZone })).toBeInTheDocument();
    expect(screen.getByText(settings.dangerDescription)).toBeInTheDocument();
    expect(note(plain(settings.readOnly, { permission }))).toBeInTheDocument();
    expect(container.querySelector("code")).toHaveTextContent(permission);
    expect(screen.getByLabelText("Issuer URL")).toBeDisabled();
    expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://auth.example.com/realms/acme");
    expect(screen.getByRole("button", { name: "Caller-owned destructive action" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: settings.save })).toBeNull();
    expect(screen.queryByRole("button", { name: settings.cancel })).toBeNull();
    fireEvent.submit(container.querySelector("form")!);
    expect(save).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it("keeps read-only hide/show and allowed behavior, including caller-supplied verbs", () => {
    const { rerender, container } = render(
      provider(
        <Restricted allowed={false} permission={permission} mode="show">
          <input aria-label="endpoint" defaultValue="https://private.example.com" />
        </Restricted>
      )
    );
    expect(
      note(plain(restricted.needsPermission, { permission, verb: restricted.changing }))
    ).toBeInTheDocument();
    expect(container.querySelector("code")).toHaveTextContent(permission);
    expect(screen.getByLabelText("endpoint")).toBeDisabled();
    rerender(
      provider(
        <Restricted allowed={false} permission={permission} mode="hide">
          <input aria-label="endpoint" />
        </Restricted>
      )
    );
    expect(screen.queryByLabelText("endpoint")).toBeNull();
    expect(container.querySelector("code")).toBeNull();
    rerender(
      provider(
        <Restricted allowed permission={permission} mode="hide">
          <input aria-label="endpoint" />
        </Restricted>
      )
    );
    expect(screen.getByLabelText("endpoint")).toBeEnabled();
    expect(container.querySelector("code")).toBeNull();
    rerender(provider(<PermissionNote permission={permission} verb="CUSTOM_OPERATION_LABEL" />));
    expect(
      note(plain(restricted.needsPermission, { permission, verb: "CUSTOM_OPERATION_LABEL" }))
    ).toBeInTheDocument();
  });

  it("translates save/cancel/pending defaults while retaining drafts, raw errors and retry callbacks", () => {
    const save = vi.fn();
    const cancel = vi.fn();
    const ui = (saving: boolean, saveLabel?: string) =>
      provider(
        <Editor
          onSave={save}
          onCancel={cancel}
          saving={saving}
          saveLabel={saveLabel}
          error={serverError}
        />
      );
    const { rerender, container } = render(ui(false));
    fireEvent.change(screen.getByLabelText("Issuer URL"), {
      target: { value: "https://draft.example.com" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(serverError);
    fireEvent.click(screen.getByRole("button", { name: settings.save }));
    expect(save).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://draft.example.com");
    rerender(ui(true));
    expect(screen.getByRole("button", { name: settings.saving })).toBeDisabled();
    expect(screen.getByRole("button", { name: settings.cancel })).toBeDisabled();
    expect(screen.getByLabelText("Issuer URL")).toBeDisabled();
    fireEvent.submit(container.querySelector("form")!);
    expect(save).toHaveBeenCalledTimes(1);
    rerender(ui(false, "CUSTOM_SAVE_LABEL"));
    expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://draft.example.com");
    expect(screen.getByRole("alert")).toHaveTextContent(serverError);
    fireEvent.click(screen.getByRole("button", { name: "CUSTOM_SAVE_LABEL" }));
    expect(save).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: settings.cancel }));
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("preserves ICU argument and rich code contracts in every translated sentence", () => {
    for (const [message, args] of [
      [settings.readOnly, ["permission"]],
      [restricted.needsPermission, ["permission", "verb"]],
    ] as const) {
      const ast = parse(message);
      const actualArgs: string[] = [];
      const tags: string[] = [];
      function collect(nodes: ReturnType<typeof parse>) {
        for (const node of nodes) {
          if (node.type === TYPE.argument) actualArgs.push(node.value);
          if (node.type === TYPE.tag) {
            tags.push(node.value);
            collect(node.children);
          }
        }
      }
      collect(ast);
      expect(actualArgs.sort()).toEqual([...args].sort());
      expect(tags).toEqual(["code"]);
    }
    if (locale !== "en") {
      for (const key of Object.keys(settings) as (keyof typeof settings)[])
        expect(settings[key]).not.toBe(en.shared.settings[key]);
      for (const key of Object.keys(restricted) as (keyof typeof restricted)[])
        expect(restricted[key]).not.toBe(en.shared.restricted[key]);
    }
  });
});

it("switches locale in place without losing a draft or changing explicit field labels", () => {
  const save = vi.fn();
  const cancel = vi.fn();
  const ui = (locale: "en" | "ja") => (
    <NextIntlClientProvider locale={locale} messages={locales[locale]}>
      <Editor onSave={save} onCancel={cancel} />
    </NextIntlClientProvider>
  );
  const { rerender } = render(ui("en"));
  fireEvent.change(screen.getByLabelText("Issuer URL"), {
    target: { value: "https://unsaved.example.com" },
  });
  rerender(ui("ja"));
  expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://unsaved.example.com");
  fireEvent.click(screen.getByRole("button", { name: ja.shared.settings.save }));
  expect(save).toHaveBeenCalledTimes(1);
});

it("hides the page's restricted fields and danger actions while retaining the permission notice", () => {
  render(
    <NextIntlClientProvider locale="fr" messages={fr}>
      <SettingsPage
        sections={[
          { id: "secret", title: "PRIVATE_SETTING", content: <input aria-label="PRIVATE_FIELD" /> },
        ]}
        dangerZone={<button>PRIVATE_ACTION</button>}
        readOnly={{ permission }}
        restrictedMode="hide"
      />
    </NextIntlClientProvider>
  );
  expect(screen.queryByLabelText("PRIVATE_FIELD")).toBeNull();
  expect(screen.queryByText("PRIVATE_SETTING")).toBeNull();
  expect(screen.queryByRole("button", { name: "PRIVATE_ACTION" })).toBeNull();
  expect(note(plain(fr.shared.settings.readOnly, { permission }))).toBeInTheDocument();
});
