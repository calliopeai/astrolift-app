import { act } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { ModelHostingSourcePanel } from "./ModelHostingSourcePanel";
import { LocalModelImportPanel } from "./LocalModelImportPanel";
import { fakeModelPage } from "./shared-model.fixtures";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
describe("model source server/client locale consistency", () => {
  it.each(Object.keys(catalogs) as (keyof typeof catalogs)[])(
    "%s hydrates local/HF controls without guessing access or exposing a secret",
    async (locale) => {
      const failures: unknown[] = [];
      const sourceProps = {
        scopeKey: "org:actor",
        allowed: true,
        authorityError: null,
        onRetryAuthority: vi.fn(),
        connections: [],
        selectedConnection: null,
        connectionsLoading: false,
        connectionsError: null,
        connectionPage: 1,
        connectionPages: 1,
        onConnectionPage: vi.fn(),
        onRetryConnections: vi.fn(),
        onSelectConnection: vi.fn(),
        onConnect: vi.fn(async () => ({
          accepted: true as const,
          account: null,
          current: true,
          refreshFailed: false,
        })),
        onManualSource: vi.fn(),
      };
      const tree = (
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
          <ModelHostingSourcePanel {...sourceProps} />
          <LocalModelImportPanel
            scopeKey="org:actor"
            allowed={true}
            phase={{ kind: "canceled" }}
            page={fakeModelPage({ rows: [] })}
            onImport={vi.fn(async () => {})}
            onCancel={vi.fn()}
            onUseArtifact={vi.fn()}
          />
        </NextIntlClientProvider>
      );
      const container = document.createElement("div");
      container.innerHTML = renderToString(tree);
      document.body.append(container);
      const before = container.textContent;
      let root: ReturnType<typeof hydrateRoot> | undefined;
      try {
        await act(async () => {
          root = hydrateRoot(container, tree, {
            onRecoverableError: (error) => failures.push(error),
          });
        });
        expect(container.textContent).toBe(before);
        expect(failures).toEqual([]);
        expect(container.textContent).toContain(
          catalogs[locale].models.shared.localImport.canceled
        );
        expect(container.textContent).toContain(
          catalogs[locale].models.shared.hosting.noConnections
        );
        const password = container.querySelector('input[type="password"]');
        expect(password).toHaveValue("");
        expect(container.textContent).not.toContain("hf_");
      } finally {
        await act(async () => root?.unmount());
        container.remove();
      }
    }
  );
});
