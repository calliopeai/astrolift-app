import { fireEvent, screen, within } from "@testing-library/react";
import { createTranslator } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import en from "@/messages/en.json";
import { renderWithIntl } from "@/test/render-with-intl";

import { SCOPED_SAFETY_FIXTURE } from "./fixtures";
import { describeRule, ScopedSafetyRules } from "./ScopedSafetyRules";

async function pick(label: string, option: string) {
  fireEvent.click(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

describe("Team, app and person action-safety rules (#2203)", () => {
  it("lists each rule with its scope and what it adds, and removes one", () => {
    const onClear = vi.fn();
    renderWithIntl(<ScopedSafetyRules {...SCOPED_SAFETY_FIXTURE} onClear={onClear} />);
    const team = screen.getByRole("listitem", { name: "Deregister an app: Payments" });
    expect(within(team).getByText("needs 2 approvers · no self-approval")).toBeInTheDocument();
    fireEvent.click(within(team).getByRole("button", { name: "Remove" }));
    expect(onClear).toHaveBeenCalledWith(SCOPED_SAFETY_FIXTURE.rules[1]);
  });

  it("adds approval for an app picked from the tree", async () => {
    const onAdd = vi.fn(async () => true);
    renderWithIntl(<ScopedSafetyRules {...SCOPED_SAFETY_FIXTURE} onAdd={onAdd} />);
    const add = screen.getByRole("button", { name: "Add rule" });
    expect(add).toBeDisabled();

    await pick("Applies to", "App");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search Apps" }), {
      target: { value: "billing" },
    });
    fireEvent.click(screen.getByRole("treeitem", { name: /billing-api/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Require approval" }));
    await pick("Approvers needed", "3 approvers");
    fireEvent.click(add);

    expect(onAdd).toHaveBeenCalledWith(
      { kind: "APP", id: "0192a3b4-0000-7000-8000-0000000000a1" },
      "app.deregister",
      { requireApproval: true, minimumApprovals: 3 }
    );
  });

  it("finds a person by search and only offers approval on approvable actions", async () => {
    const onAdd = vi.fn(async () => true);
    const onQuery = vi.fn();
    renderWithIntl(
      <ScopedSafetyRules
        {...SCOPED_SAFETY_FIXTURE}
        people={{ ...SCOPED_SAFETY_FIXTURE.people, onQuery }}
        onAdd={onAdd}
      />
    );
    await pick("Applies to", "Person");
    fireEvent.change(screen.getByRole("textbox", { name: "Search people" }), {
      target: { value: "dana" },
    });
    expect(onQuery).toHaveBeenCalledWith("dana");
    fireEvent.click(screen.getByRole("button", { name: /Dana Ortiz/ }));

    await pick("Action", "Reveal a secret");
    expect(screen.queryByRole("checkbox", { name: "Require approval" })).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: /Ask before/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    expect(onAdd).toHaveBeenCalledWith({ kind: "USER", id: "42" }, "secret.reveal", {
      enabled: true,
    });
  });

  it("describes a rule that adds nothing honestly", () => {
    const scoped = createTranslator({
      locale: "en",
      messages: en,
      namespace: "actionSafety.scoped",
    });
    const t = (key: string) => scoped(key as never);
    expect(describeRule({ action: "x", scopeKind: "APP", scopeLabel: "a" }, t)).toBe(
      "adds nothing"
    );
  });
});
