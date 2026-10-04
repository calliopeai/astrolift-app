import { composeStories, setProjectAnnotations } from "@storybook/react";
import { NextIntlClientProvider } from "next-intl";
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
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
import * as stories from "./NativeModelConnectScreen.stories";
import * as settings from "./NativeModelSettingsPanel.stories";
import { NativeModelSettingsPanel } from "./NativeModelSettingsPanel";
import { NativeModelObservationsPanel } from "./NativeModelObservationsPanel";
import { SharedModelsScreen } from "./SharedModelsScreen";
import { SharedModelDetailScreen } from "./SharedModelDetailScreen";
import { sharedModelsProps } from "./shared-models.fixtures";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { nativeSource, nativeModel, nativeModelRow } from "./native-model.fixtures";

setProjectAnnotations(preview);
afterEach(cleanup);
const rendered = composeStories(stories);
it.each(Object.entries({ en, es, fr, de, ja, ko, "pt-BR": pt, "zh-Hans": zh }))(
  "%s renders placement, bounded discovery and registration review without missing translations",
  (locale, messages) => {
    const onError = vi.fn();
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC" onError={onError}>
        <rendered.Placement /> <rendered.BoundedPartial /> <rendered.Review />
      </NextIntlClientProvider>
    );
    expect(view.container.textContent).toContain(nativeSource.identity.sourceArn);
    expect(view.container.textContent).toContain(nativeSource.identity.sourceFingerprint);
    expect(view.container.querySelectorAll("table")).toHaveLength(2);
    expect(view.container.querySelectorAll('input[type="checkbox"]')).toHaveLength(1);
    expect(onError).not.toHaveBeenCalled();
  }
);
it.each(Object.entries(rendered))("portable native wizard story %s", async (_name, Story) => {
  const canvas = document.createElement("div");
  document.body.append(canvas);
  try {
    await runStory(Story, canvas);
    expect(canvas.childElementCount).toBeGreaterThan(0);
  } finally {
    canvas.remove();
  }
});

it.each(Object.entries({ en, es, fr, de, ja, ko, "pt-BR": pt, "zh-Hans": zh }))(
  "%s shows immutable native metadata instead of fabricated runtime resources",
  (locale, messages) => {
    const onError = vi.fn();
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC" onError={onError}>
        <SharedModelsScreen page={{ ...sharedModelsProps.page, rows: [nativeModelRow] }} />
        <SharedModelDetailScreen
          {...sharedModelDetailProps}
          model={nativeModel}
          prompt={null}
          observations={<NativeModelObservationsPanel />}
          management={<NativeModelSettingsPanel {...settings.nativeSettingsProps} />}
        />
      </NextIntlClientProvider>
    );
    expect(view.container.textContent).toContain(nativeSource.identity.sourceFingerprint);
    expect(view.container.textContent).toContain(nativeSource.identity.sourceArn);
    expect(view.container.textContent).toContain(nativeSource.identity.accountId);
    expect(view.container.textContent).not.toContain(messages.models.shared.detail.resourcesHelp);
    expect(view.container.textContent).not.toContain(messages.models.shared.detail.runtimeUnknown);
    expect(view.container.querySelectorAll('#model-settings input[type="radio"]')).toHaveLength(2);
    expect(onError).not.toHaveBeenCalled();
  }
);
it.each(Object.entries(composeStories(settings)))(
  "portable native settings story %s",
  async (_name, Story) => {
    const canvas = document.createElement("div");
    document.body.append(canvas);
    try {
      await runStory(Story, canvas);
      expect(canvas.childElementCount).toBeGreaterThan(0);
    } finally {
      canvas.remove();
    }
  }
);

it.each(Object.entries({ en, es, fr, de, ja, ko, "pt-BR": pt, "zh-Hans": zh }))(
  "%s retains unavailable Bedrock identity without hosted fallback",
  (locale, messages) => {
    const onError = vi.fn();
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC" onError={onError}>
        <SharedModelsScreen
          page={{ ...sharedModelsProps.page, rows: [{ ...nativeModelRow, nativeSource: null }] }}
        />
        <SharedModelDetailScreen
          {...sharedModelDetailProps}
          model={{ ...nativeModel, nativeSource: null }}
          prompt={null}
        />
      </NextIntlClientProvider>
    );
    expect(view.container.textContent).toContain(messages.models.native.details.unavailable);
    expect(view.container.textContent).toContain("Amazon Bedrock");
    expect(view.container.textContent).not.toContain(messages.models.native.details.unsupported);
    expect(view.container.textContent).not.toContain(messages.models.shared.detail.resourcesHelp);
    expect(view.container.textContent).not.toContain(nativeSource.identity.sourceArn);
    expect(view.container.textContent).not.toContain(nativeSource.identity.accountId);
    expect(onError).not.toHaveBeenCalled();
  }
);
