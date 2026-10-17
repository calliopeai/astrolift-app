import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import {
  CONNECTIONS,
  CREATION,
  CREATION_FAILED,
  CREATION_READY,
  POSTGRES_DRAFT,
  PROJECTS,
  wizardProps,
} from "./new-brain.fixtures";
import {
  draftProblems,
  EMPTY_DRAFT,
  type NewBrainStep,
  stepReady,
  withDefaults,
} from "./new-brain-model";
import { NewBrainWizard, type NewBrainWizardProps } from "./NewBrainWizard";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
type Locale = keyof typeof catalogs;

function inLocale(locale: Locale, ui: React.ReactElement, onError = vi.fn()) {
  return render(
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      onError={onError}
    >
      {ui}
    </NextIntlClientProvider>
  );
}

const PROGRESS = (patch: Partial<NonNullable<NewBrainWizardProps["progress"]>>) =>
  wizardProps(4, {
    progress: {
      creation: CREATION,
      loading: false,
      error: null,
      onRetry: () => {},
      deploy: true,
      failed: null,
      onEdit: () => {},
      onStartOver: () => {},
      ...patch,
    },
  });

describe("the create wizard in every locale (#2427)", () => {
  it.each(locales)("%s renders every step and every progress state in its own words", (locale) => {
    const onError = vi.fn();
    const copy = catalogs[locale as Locale].brains;
    const states: NewBrainWizardProps[] = [
      wizardProps(1, { serverErrors: { repo: "server says no" } }),
      wizardProps(2, { canDeploy: false }),
      wizardProps(3, {
        draft: POSTGRES_DRAFT,
        substrateError: { field: "persistence", message: "no" },
      }),
      wizardProps(4, { generalError: "the workflow engine is off on this install" }),
      wizardProps(1, { denied: true }),
      PROGRESS({}),
      PROGRESS({ creation: CREATION_READY }),
      PROGRESS({ creation: CREATION_FAILED, failed: "REPO_READY" }),
      PROGRESS({ creation: null }),
      PROGRESS({ creation: null, error: "Network error" }),
      PROGRESS({ error: "Network error" }),
    ];
    for (const props of states) {
      const view = inLocale(locale as Locale, <NewBrainWizard {...props} />, onError);
      view.unmount();
    }
    inLocale(locale as Locale, <NewBrainWizard {...wizardProps(3)} />, onError);
    expect(screen.getAllByText(copy.wizard.steps.substrate.label).length).toBeGreaterThan(0);
    expect(screen.getByText(copy.substrate.axes.authority)).toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();
    if (locale !== "en")
      expect(copy.wizard.steps.source.description).not.toBe(
        en.brains.wizard.steps.source.description
      );
  });
});

/** The wizard as useNewBrain drives it, with its create call recorded. */
function Live({ onCreate, start = 1 }: { onCreate: () => void; start?: NewBrainStep }) {
  const [step, setStep] = React.useState<NewBrainStep>(start);
  const [own, setOwn] = React.useState(EMPTY_DRAFT);
  const context = { connections: CONNECTIONS, projects: PROJECTS };
  const draft = withDefaults(own, context);
  const problems = draftProblems(draft, context);
  return (
    <NewBrainWizard
      {...wizardProps(step, {
        draft,
        onChange: setOwn,
        problems,
        onStep: setStep,
        onBack: step > 1 ? () => setStep((step - 1) as NewBrainStep) : undefined,
        onContinue: () => (step < 4 ? setStep((step + 1) as NewBrainStep) : onCreate()),
        continueDisabled: !stepReady(step, problems),
      })}
    />
  );
}

describe("the create wizard", () => {
  it("holds Continue until the step's own fields are filled in", () => {
    inLocale("en", <Live onCreate={() => {}} />);
    const next = () => screen.getByRole("button", { name: en.brains.wizard.continue });
    expect(next()).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Repository/), { target: { value: "acme platform" } });
    expect(screen.getByRole("alert")).toHaveTextContent(en.brains.wizard.problems.repoFormat);
    expect(next()).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Repository/), { target: { value: "acme/handbook" } });
    expect(next()).toBeEnabled();
    fireEvent.click(next());
    expect(
      screen.getByRole("heading", { name: en.brains.wizard.steps.details.label })
    ).toBeInTheDocument();
    // Step 2 needs a slug; the name suggests one.
    expect(next()).toBeDisabled();
    fireEvent.change(screen.getByLabelText(en.brains.wizard.name), {
      target: { value: "Handbook" },
    });
    expect(screen.getByLabelText(/Slug/)).toHaveValue("handbook");
    expect(next()).toBeEnabled();
  });

  it("shows a server refusal on the field it names", () => {
    inLocale(
      "en",
      <NewBrainWizard
        {...wizardProps(2, {
          serverErrors: { slug: "app with slug 'platform-brain' already exists" },
        })}
      />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("already exists");
    expect(screen.getByLabelText(/Slug/)).toHaveAttribute("aria-invalid", "true");
  });

  it("offers only supported cells, and holds what the cell needs", () => {
    inLocale("en", <NewBrainWizard {...wizardProps(3, { draft: POSTGRES_DRAFT })} />);
    const postgres = screen.getByRole("checkbox", { name: "postgres" });
    expect(postgres).toBeChecked();
    expect(postgres).toBeDisabled();
    expect(screen.getByText(/Supported by Mimwell/)).toBeInTheDocument();
  });

  it("says a production deploy asks for a fresh sign-in, and leaves it out without app.deploy", () => {
    const { rerender } = inLocale("en", <NewBrainWizard {...wizardProps(4)} />);
    expect(screen.getByText(en.brains.wizard.productionStepUp)).toBeInTheDocument();
    rerender(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <NewBrainWizard {...wizardProps(4, { canDeploy: false })} />
      </NextIntlClientProvider>
    );
    expect(screen.queryByText(en.brains.wizard.productionStepUp)).not.toBeInTheDocument();
    expect(screen.getByText(en.brains.wizard.deployNo)).toBeInTheDocument();
  });

  it("offers no form without app.create", () => {
    inLocale("en", <NewBrainWizard {...wizardProps(1, { denied: true })} />);
    expect(screen.getByText(en.brains.wizard.deniedTitle)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: en.brains.wizard.continue })
    ).not.toBeInTheDocument();
  });

  it("follows a creation to READY and opens the layer from there", () => {
    inLocale("en", <NewBrainWizard {...PROGRESS({ creation: CREATION_READY })} />);
    expect(screen.getByRole("link", { name: new RegExp(en.brains.creation.open) })).toHaveAttribute(
      "href",
      "/brains/platform-brain"
    );
    const steps = within(screen.getByRole("region", { name: en.brains.creation.progress }));
    expect(steps.getAllByText(en.brains.creation.stepStates.done)).toHaveLength(4);
  });

  it("shows a failure's reason and the way back to the form", () => {
    const onEdit = vi.fn();
    const onStartOver = vi.fn();
    inLocale(
      "en",
      <NewBrainWizard
        {...PROGRESS({ creation: CREATION_FAILED, failed: "REPO_READY", onEdit, onStartOver })}
      />
    );
    expect(
      screen.getByText(/PRECONDITION: .*GitHub App cannot create repositories/)
    ).toBeInTheDocument();
    expect(screen.getByText(en.brains.creation.stepStates.failed)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.brains.creation.editAndRetry }));
    fireEvent.click(screen.getByRole("button", { name: en.brains.creation.startOver }));
    expect(onEdit).toHaveBeenCalled();
    expect(onStartOver).toHaveBeenCalled();
  });
});
