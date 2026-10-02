import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, expect, it } from "vitest";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import de from "@/messages/de.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { CollectionStageOptions } from "./CollectionStageOptions";

afterEach(cleanup);
for (const [locale, messages] of [
  ["en", en],
  ["es", es],
  ["de", de],
  ["fr", fr],
  ["ja", ja],
  ["ko", ko],
  ["pt-BR", pt],
  ["zh-Hans", zh],
] as const) {
  it(`preserves source collection and body identity while changing cap in ${locale}`, () => {
    let submitted: unknown;
    const original = {
      source_format: "langflow_loop",
      max_items: 3,
      body_end: "body",
      items: [{ text: "first" }, { text: "second" }],
    };
    render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <CollectionStageOptions
          kind="collection"
          iteration={original}
          targets={["body"]}
          disabled={false}
          onChange={(value) => {
            submitted = value;
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByLabelText(messages.workflowCollections.bodyEnd)).toBeDisabled();
    fireEvent.change(screen.getByLabelText(messages.workflowCollections.cap), {
      target: { value: "4" },
    });
    expect(submitted).toEqual({ ...original, max_items: 4 });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
}

it("reports oversized fixed inputs and invalid forward targets", () => {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <CollectionStageOptions
        kind="collection"
        iteration={{ max_items: 1, body_end: "missing", items: [{}, {}] }}
        targets={["body"]}
        disabled={false}
        onChange={() => {}}
      />
    </NextIntlClientProvider>
  );
  expect(screen.getByRole("alert")).toHaveTextContent(en.workflowCollections.invalid);
});

it("edits the source parser pattern without changing its result contract", () => {
  let submitted: unknown;
  const original = { source_format: "langflow_parser", pattern: "Item: {text}", separator: "\n" };
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <CollectionStageOptions
        kind="format_record"
        iteration={original}
        targets={[]}
        disabled={false}
        onChange={(value) => {
          submitted = value;
        }}
      />
    </NextIntlClientProvider>
  );
  fireEvent.change(screen.getByLabelText(en.workflowCollections.pattern), {
    target: { value: "Record: {text}" },
  });
  expect(submitted).toEqual({ ...original, pattern: "Record: {text}" });
  expect(screen.getByLabelText(en.workflowCollections.separator)).toHaveValue('"\\n"');
});
