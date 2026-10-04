import { act } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { SharedModelManagementPanel } from "./SharedModelManagementPanel";
import { sharedModelManagementProps } from "./shared-model-management.fixtures";
import { modelSettingsDraft, modelSettingsRequest } from "./shared-model-settings";
const locales = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
describe("stored control locale hydration", () => {
  it.each(Object.entries(locales))(
    "%s preserves recorded unknown values and inputs across hydration",
    async (locale, messages) => {
      const model = {
        ...sharedModelManagementProps.model,
        name: "<Literal & Model>",
        desiredResources: {
          ...sharedModelManagementProps.model.desiredResources,
          dtype: "vendor_Future_Value",
          maxModelLen: 1024,
          maxNumSeqs: 3,
        },
      };
      const draft = modelSettingsDraft(model);
      const view = (
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="America/Costa_Rica">
          <SharedModelManagementPanel
            {...sharedModelManagementProps}
            model={model}
            draft={draft}
            admission={{
              ...sharedModelManagementProps.admission!,
              requestKey: JSON.stringify(modelSettingsRequest(model, draft)),
            }}
          />
        </NextIntlClientProvider>
      );
      const html = renderToString(view);
      expect(html).toContain("vendor_Future_Value");
      expect(html).toContain("&lt;Literal &amp; Model&gt;");
      const element = document.createElement("div");
      element.innerHTML = html;
      document.body.append(element);
      const recover = vi.fn(),
        root = hydrateRoot(element, view, { onRecoverableError: recover });
      try {
        await act(async () => {});
        expect(recover).not.toHaveBeenCalled();
        const ids = Array.from(element.querySelectorAll("label"))
          .map((label) => label.htmlFor)
          .filter(Boolean);
        expect(ids.every((id) => !!element.querySelector(`[id="${id}"]`))).toBe(true);
        expect(element.querySelector("select")?.value).toBe("");
        expect(element.textContent).toContain(messages.models.shared.storedControls.preserveHelp);
      } finally {
        await act(async () => root.unmount());
        element.remove();
      }
    }
  );
});
