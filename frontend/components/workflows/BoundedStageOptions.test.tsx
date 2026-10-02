import { cleanup, fireEvent, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import de from "@/messages/de.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { BoundedStageOptions } from "./BoundedStageOptions";
import { edgeFromDraft, readBackEdge } from "./back-edge";

const rows = [
  ["en", en],
  ["es", es],
  ["de", de],
  ["fr", fr],
  ["ja", ja],
  ["ko", ko],
  ["pt-BR", pt],
  ["zh-Hans", zh],
] as const;
afterEach(cleanup);

describe("bounded authoring", () => {
  for (const [locale, messages] of rows)
    it(`edits explicit target, rounds and exhaustion in ${locale}`, () => {
      const onChange = vi.fn();
      render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <BoundedStageOptions
            kind="human_gate"
            maxAttempts={3}
            backEdge={{ to: "draft", when: "gate_rejected", max_rounds: 5, on_exhausted: "fail" }}
            targets={["draft", "assessment"]}
            disabled={false}
            onChange={onChange}
          />
        </NextIntlClientProvider>
      );
      const t = messages.workflowBounds;
      expect(screen.getByLabelText(t.rounds)).toHaveValue(5);
      fireEvent.change(screen.getByLabelText(t.target), { target: { value: "assessment" } });
      expect(onChange).toHaveBeenLastCalledWith({
        backEdge: { to: "assessment", when: "gate_rejected", max_rounds: 5, on_exhausted: "fail" },
      });
      fireEvent.change(screen.getByLabelText(t.exhausted), { target: { value: "escalate" } });
      expect(onChange).toHaveBeenLastCalledWith({
        backEdge: { to: "draft", when: "gate_rejected", max_rounds: 5, on_exhausted: "escalate" },
      });
      expect(screen.queryByLabelText(t.attempts)).not.toBeInTheDocument();
    });

  it("does not allow a return without a prior stable output key", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <BoundedStageOptions
          kind="agent_dispatch"
          maxAttempts={4}
          backEdge={{}}
          targets={[]}
          disabled={false}
          onChange={vi.fn()}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByLabelText(en.workflowBounds.attempts)).toHaveValue(4);
  });

  it("preserves typed false/null values and rejects containers or malformed JSON before mutation", () => {
    const base = {
      to: "draft",
      when: "output_equals",
      path: "tests.passed",
      value: true,
      max_rounds: 3,
      on_exhausted: "fail",
    };
    expect(edgeFromDraft(base, "false")).toEqual({ ...base, value: false });
    expect(edgeFromDraft(base, "null")).toEqual({ ...base, value: null });
    expect(() => edgeFromDraft(base, "{}")).toThrow();
    expect(() => edgeFromDraft(base, "falsex")).toThrow();
    expect(readBackEdge({ ...base, max_rounds: true })).toBeNull();
  });
});

it("shows an existing typed null condition as null", () => {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <BoundedStageOptions
        kind="checkpoint"
        maxAttempts={3}
        backEdge={{
          to: "draft",
          when: "output_equals",
          path: "value",
          value: null,
          max_rounds: 2,
          on_exhausted: "fail",
        }}
        targets={["draft"]}
        disabled={false}
        onChange={vi.fn()}
      />
    </NextIntlClientProvider>
  );
  expect(screen.getByLabelText(en.workflowBounds.value)).toHaveValue("null");
});

it("preserves imported source semantics when editing its bounded cap", () => {
  const onChange = vi.fn();
  const edge = {
    to: "flow_humaninputagentflow_0",
    when: "always",
    max_rounds: 5,
    on_exhausted: "continue",
    source_format: "flowise_loop_1_2",
    source_target: "humanInputAgentflow_0",
    source_label: "Continue this round?",
    fallback_message: null,
  };
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <BoundedStageOptions
        kind="checkpoint"
        maxAttempts={3}
        backEdge={edge}
        targets={["flow_humaninputagentflow_0", "another"]}
        disabled={false}
        onChange={onChange}
      />
    </NextIntlClientProvider>
  );
  for (const key of ["target", "trigger", "exhausted"] as const)
    expect(screen.getByLabelText(en.workflowBounds[key])).toBeDisabled();
  expect(screen.getByLabelText(en.workflowBounds.trigger)).toHaveValue("always");
  expect(screen.getByLabelText(en.workflowBounds.exhausted)).toHaveValue("continue");
  fireEvent.change(screen.getByLabelText(en.workflowBounds.rounds), { target: { value: "2" } });
  expect(onChange).toHaveBeenLastCalledWith({ backEdge: { ...edge, max_rounds: 2 } });
  expect(readBackEdge({ ...edge, to: "another" })).toBeNull();
  expect(readBackEdge({ ...edge, source_label: undefined })).toBeNull();
  expect(readBackEdge({ ...edge, source_format: undefined })).toBeNull();
  expect(
    readBackEdge({ to: "draft", when: "always", max_rounds: 2, on_exhausted: "continue" })
  ).toBeNull();
});
