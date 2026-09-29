import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { IMPORT_RESULT, IMPORT_SKILLS, LONG, LONG_URL, SHA64 } from "./agent-skills.fixtures";
import { ImportSkillsSheet } from "./ImportSkillsSheet";

const meta: Meta = { title: "Screens/Agents/Skills/ImportSkillsSheet" };
export default meta;

type Story = StoryObj;

const open = { open: true, onOpenChange: () => {} };

export const Blank: Story = { render: () => <ImportSkillsSheet {...IMPORT_SKILLS} {...open} /> };

export const Importing: Story = {
  render: () => <ImportSkillsSheet {...IMPORT_SKILLS} {...open} loading />,
};

/** The server refused the repo: the reason stands beside the field, not in a toast. */
export const FieldError: Story = {
  render: () => (
    <ImportSkillsSheet
      {...IMPORT_SKILLS}
      {...open}
      initialErrors={{ repoUrl: "Only github.com repositories can be imported." }}
    />
  ),
};

/** A refusal with no field leads the form. */
export const FormError: Story = {
  render: () => (
    <ImportSkillsSheet
      {...IMPORT_SKILLS}
      {...open}
      initialErrors={{ form: "astrolift.toml not found at the repository root." }}
    />
  ),
};

export const Imported: Story = {
  render: () => <ImportSkillsSheet {...IMPORT_SKILLS} {...open} result={IMPORT_RESULT} />,
};

/** Nothing declared: both lists say none. */
export const ImportedNothing: Story = {
  render: () => (
    <ImportSkillsSheet
      {...IMPORT_SKILLS}
      {...open}
      result={{ importedSkills: [], importedTools: [], sourceRef: "acme/empty@main" }}
    />
  ),
};

/** A long source ref and slugs: an unbroken URL, a 64-char SHA ref. */
export const LongStrings: Story = {
  render: () => (
    <ImportSkillsSheet
      {...IMPORT_SKILLS}
      {...open}
      result={{
        importedSkills: [`${LONG}-${SHA64}`, ...IMPORT_RESULT.importedSkills],
        importedTools: [LONG_URL],
        sourceRef: `${LONG}/agent-config@${SHA64}`,
      }}
    />
  ),
};

/** Import with no URL is blocked before the server is asked. */
export const NeedsUrl: Story = {
  render: () => <ImportSkillsSheet {...IMPORT_SKILLS} {...open} />,
  play: async () => {
    const body = within(document.body);
    await expect(await body.findByRole("button", { name: "Import" })).toBeDisabled();
    await userEvent.type(body.getByLabelText("Repository URL"), "https://github.com/acme/x");
    await expect(body.getByRole("button", { name: "Import" })).toBeEnabled();
  },
};
