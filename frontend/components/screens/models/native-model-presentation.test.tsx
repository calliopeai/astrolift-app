import { composeStories, setProjectAnnotations } from "@storybook/react";
import { NextIntlClientProvider } from "next-intl";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { runStory } from "@/test/run-story";
import * as preview from "../../../.storybook/preview";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";
import { ModelAddChoicePanel } from "./ModelAddChoicePanel";
import { NativeModelObservationsPanel } from "./NativeModelObservationsPanel";
import * as choices from "./ModelAddChoicePanel.stories";
import * as observations from "./NativeModelObservationsPanel.stories";

setProjectAnnotations(preview);
afterEach(cleanup);
describe("native model presentation", () => {
  it.each(Object.entries({ en, es, fr, de, ja, ko, "pt-BR": pt, "zh-Hans": zh }))(
    "%s keeps unverified observations separate from actionable permissions",
    (locale, messages) => {
      const onError = vi.fn();
      const props = {
        hosting: { allowed: true as boolean | null, reason: null },
        connection: { allowed: null as boolean | null, reason: null },
      };
      const view = render(
        <NextIntlClientProvider
          locale={locale}
          messages={messages}
          timeZone="UTC"
          onError={onError}
        >
          <ModelAddChoicePanel {...props} />
          <NativeModelObservationsPanel />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("link")).toHaveAttribute("href", "/models/deploy");
      expect(screen.getByRole("button")).toBeDisabled();
      expect(document.querySelectorAll("#model-metrics dd")).toHaveLength(3);
      expect(document.querySelector("#model-metrics button")).toBeNull();
      expect(onError).not.toHaveBeenCalled();
      view.rerender(
        <NextIntlClientProvider
          locale={locale}
          messages={messages}
          timeZone="UTC"
          onError={onError}
        >
          <ModelAddChoicePanel
            hosting={{ allowed: false, reason: "Current hosting authority withdrawn" }}
            connection={{ allowed: false, reason: "Native connections disabled" }}
          />
        </NextIntlClientProvider>
      );
      expect(screen.queryByRole("link")).toBeNull();
      for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
      expect(screen.getByText("Current hosting authority withdrawn")).toBeVisible();
      expect(screen.getByText("Native connections disabled")).toBeVisible();
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each(
    Object.entries({
      ...composeStories(choices),
      ...Object.fromEntries(
        Object.entries(composeStories(observations)).map(([key, story]) => [
          `Observations${key}`,
          story,
        ])
      ),
    })
  )("portable story %s", async (_name, Story) => {
    const canvas = document.createElement("div");
    document.body.append(canvas);
    try {
      await runStory(Story, canvas);
      expect(canvas.childElementCount).toBeGreaterThan(0);
    } finally {
      canvas.remove();
    }
  });
});
