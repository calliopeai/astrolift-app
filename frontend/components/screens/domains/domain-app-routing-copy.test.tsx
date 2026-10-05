import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider, createTranslator } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import {
  DomainAppConnectionSheet,
  type DomainAppConnectionSheetProps,
} from "./DomainAppConnectionSheet";
import { APP_CONNECTION, HTTP_API_CONNECTION } from "./DomainAppConnectionSheet.stories";
import { DomainDefaultSettings } from "./DomainDefaultSettings";
import { DEFAULT_SETTINGS } from "./DomainDefaultSettings.stories";

const catalogues = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const routeKeys =
  "connect intro steps hostnameStep targetStep reviewStep apex subdomain subdomainHelp next targetHelp empty selectTarget previousPage nextPage review reviewHelp workload dnsProposal manualHelp prepare attach unknownHelp recordedHelp observationHelp recover targetKind deploymentHosting httpApiHosting endpoint tlsStatus endpointReviewHelp routeObservationHelp targetPending dnsUnavailable apiId apiStage functionReference certificateStatus".split(
    " "
  );
const defaultKeys =
  "title help declared unavailable uncertain useFor review reviewHelp save reasonReadOnly reasonProof reasonProvisioning reasonWriter reasonOverride reasonPrecedence reasonPermission reasonChanged applied".split(
    " "
  );
const wrap = (children: React.ReactNode) => (
  <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);

describe("domain routing and defaults pure views", () => {
  it.each(Object.entries(catalogues))(
    "%s has exact translated keys and renders manual records separately from writes",
    (locale, messages) => {
      for (const [namespace, keys] of [
        ["domainAppRouting", routeKeys],
        ["domainDefaults", defaultKeys],
      ] as const) {
        const copy = messages[namespace];
        expect(Object.keys(copy).sort()).toEqual([...keys].sort());
        const t = createTranslator({ locale, messages, namespace });
        for (const key of keys) {
          const value = copy[key as keyof typeof copy];
          expect(value.trim()).not.toBe("");
          expect(parse(value).every((node) => node.type === TYPE.literal)).toBe(true);
          if (
            locale !== "en" &&
            !(locale === "de" && namespace === "domainAppRouting" && key === "subdomain")
          )
            expect(value).not.toBe(en[namespace][key as keyof typeof copy]);
          expect(t(key as keyof typeof copy)).toBe(value);
        }
      }
      const onError = vi.fn();
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={messages}
          timeZone="UTC"
          onError={onError}
        >
          <DomainAppConnectionSheet
            {...HTTP_API_CONNECTION}
            initialStep={2}
            review={{ ...HTTP_API_CONNECTION.review!, mode: "manual" }}
          />
          <DomainDefaultSettings
            {...DEFAULT_SETTINGS}
            reason="READ_ONLY_DOMAIN"
            canSetDefaults={false}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(messages.domainAppRouting.manualHelp)).toBeInTheDocument();
      expect(screen.getByText(messages.domainAppRouting.httpApiHosting)).toBeInTheDocument();
      expect(screen.getByText(messages.domainAppRouting.endpoint)).toBeInTheDocument();
      expect(screen.getByText(messages.domainAppRouting.tlsStatus)).toBeInTheDocument();
      expect(screen.getByText(messages.domainAppRouting.targetPending)).toBeInTheDocument();
      expect(screen.getByText(messages.domainAppRouting.certificateStatus)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: messages.domainAppRouting.attach })).toBeDisabled();
      expect(
        screen.getByRole("button", { name: messages.managedDomains.cancel })
      ).toBeInTheDocument();
      expect(screen.getByText(messages.domainDefaults.help)).toBeInTheDocument();
      expect(screen.getByText(messages.domainDefaults.reasonReadOnly)).toBeInTheDocument();
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("shows only the active step and requires a selected currently admitted target", () => {
    const onReview = vi.fn();
    render(
      wrap(<DomainAppConnectionSheet {...APP_CONNECTION} targetKey={null} onReview={onReview} />)
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: en.domainAppRouting.attach })
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.next }));
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: en.domainAppRouting.targetStep })).toHaveValue("");
    expect(screen.getByRole("button", { name: en.domainAppRouting.review })).toBeDisabled();
    expect(onReview).not.toHaveBeenCalled();
  });

  it("Escape dismisses the accessible sheet without submitting or forgetting parent-owned recovery", async () => {
    const onOpenChange = vi.fn(),
      onAttach = vi.fn();
    render(
      wrap(
        <DomainAppConnectionSheet
          {...APP_CONNECTION}
          initialStep={2}
          outcome="unknown"
          onOpenChange={onOpenChange}
          onAttach={onAttach}
        />
      )
    );
    expect(screen.getByRole("dialog", { name: en.domainAppRouting.connect })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onAttach).not.toHaveBeenCalled();
  });

  it.each(["loading", "refused", "error"] as const)(
    "%s refuses review even with stale selected rows and capabilities",
    (readState) => {
      const onReview = vi.fn();
      render(
        wrap(
          <DomainAppConnectionSheet
            {...APP_CONNECTION}
            initialStep={1}
            readState={readState}
            onReview={onReview}
          />
        )
      );
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.review }));
      expect(onReview).not.toHaveBeenCalled();
    }
  );

  it.each(["target", "hostname", "capability", "handler", "manual", "refresh"])(
    "%s mismatch refuses an attach despite old review",
    (reason) => {
      const onAttach = vi.fn();
      const props: DomainAppConnectionSheetProps = {
        ...APP_CONNECTION,
        initialStep: 2 as const,
        onAttach,
      };
      if (reason === "target") props.targetKey = "different-target";
      if (reason === "hostname") {
        props.hostnameKind = "subdomain";
        props.subdomain = "new";
      }
      if (reason === "capability") props.canAttach = false;
      if (reason === "handler") props.onAttach = undefined;
      if (reason === "manual") props.review = { ...props.review!, mode: "manual" };
      if (reason === "refresh") props.readState = "loading";
      render(wrap(<DomainAppConnectionSheet {...props} />));
      fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
      expect(onAttach).not.toHaveBeenCalled();
    }
  );

  it.each(["loading", "error"])("retained review %s cannot prepare or attach", (state) => {
    const onPrepare = vi.fn(),
      onAttach = vi.fn();
    render(
      wrap(
        <DomainAppConnectionSheet
          {...APP_CONNECTION}
          initialStep={2}
          canPrepare
          onPrepare={onPrepare}
          onAttach={onAttach}
          reviewing={state === "loading"}
          reviewError={state === "error" ? "Current review unavailable" : null}
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.prepare }));
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    expect(onPrepare).not.toHaveBeenCalled();
    expect(onAttach).not.toHaveBeenCalled();
  });

  it("unknown outcome retains recovery-only controls and locks target changes", () => {
    const onAttach = vi.fn(),
      onRecover = vi.fn();
    render(
      wrap(
        <DomainAppConnectionSheet
          {...APP_CONNECTION}
          initialStep={2}
          outcome="unknown"
          onAttach={onAttach}
          onRecover={onRecover}
        />
      )
    );
    expect(screen.getByText(en.domainAppRouting.unknownHelp)).toBeInTheDocument();
    expect(screen.getByText(en.domainAppRouting.routeObservationHelp)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.recover }));
    expect(onRecover).toHaveBeenCalledOnce();
    expect(onAttach).not.toHaveBeenCalled();
  });

  it("a declared but ineffective use can be explicitly reviewed with version and sibling fingerprint", async () => {
    const onSave = vi.fn(async () => {});
    render(wrap(<DomainDefaultSettings {...DEFAULT_SETTINGS} onSave={onSave} />));
    fireEvent.click(screen.getByRole("button", { name: en.domainDefaults.review }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(en.domainDefaults.reviewHelp);
    fireEvent.click(screen.getByRole("button", { name: en.domainDefaults.save }));
    expect(onSave).toHaveBeenCalledWith("tenant_apps", 2, "a".repeat(64));
  });

  it("defaults NONE remains available with update authority when new defaults are refused", () => {
    const onSave = vi.fn(async () => {});
    render(
      wrap(<DomainDefaultSettings {...DEFAULT_SETTINGS} canSetDefaults={false} onSave={onSave} />)
    );
    expect(screen.getByRole("button", { name: en.domainDefaults.review })).toBeDisabled();
    fireEvent.change(screen.getByRole("combobox", { name: en.domainDefaults.useFor }), {
      target: { value: "none" },
    });
    fireEvent.click(screen.getByRole("button", { name: en.domainDefaults.review }));
    fireEvent.click(screen.getByRole("button", { name: en.domainDefaults.save }));
    expect(onSave).toHaveBeenCalledWith("none", 2, "a".repeat(64));
  });

  it.each(["version", "fingerprint", "authority", "unknown"])(
    "changed %s invalidates a default review",
    (reason) => {
      const onSave = vi.fn(async () => {});
      const props = { ...DEFAULT_SETTINGS, onSave };
      const view = render(wrap(<DomainDefaultSettings {...props} />));
      fireEvent.click(screen.getByRole("button", { name: en.domainDefaults.review }));
      if (reason === "version") props.version = 3;
      if (reason === "fingerprint") props.defaultsFingerprint = "b".repeat(64);
      if (reason === "authority") props.canUpdate = false;
      if (reason === "unknown") props.uncertain = true;
      view.rerender(wrap(<DomainDefaultSettings {...props} />));
      const save = screen.queryByRole("button", { name: en.domainDefaults.save });
      if (save) fireEvent.click(save);
      expect(onSave).not.toHaveBeenCalled();
    }
  );
});

describe("HTTP API domain connection render boundaries", () => {
  it("keeps the same admitted workload picker with explicit hosting kinds", () => {
    const onTarget = vi.fn();
    render(
      wrap(
        <DomainAppConnectionSheet
          {...HTTP_API_CONNECTION}
          initialStep={1}
          targets={[...APP_CONNECTION.targets, ...HTTP_API_CONNECTION.targets]}
          onTarget={onTarget}
        />
      )
    );
    expect(
      screen.getByRole("option", {
        name: `${en.domainAppRouting.deploymentHosting} · Sample app · production · web`,
      })
    ).toHaveValue(APP_CONNECTION.targetKey);
    expect(
      screen.getByRole("option", {
        name: `${en.domainAppRouting.httpApiHosting} · Sample function app · production · public-handler`,
      })
    ).toHaveValue(HTTP_API_CONNECTION.targetKey);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: APP_CONNECTION.targetKey } });
    expect(onTarget).toHaveBeenCalledExactlyOnceWith(APP_CONNECTION.targetKey);
  });
  it("discloses pending native allocation without inventing an ALIAS or another approval step", () => {
    const onAttach = vi.fn();
    render(
      wrap(
        <DomainAppConnectionSheet
          {...HTTP_API_CONNECTION}
          initialStep={2}
          review={{ ...HTTP_API_CONNECTION.review!, certificate: "Issued" }}
          canAttach
          onAttach={onAttach}
        />
      )
    );
    expect(screen.getByText(en.domainAppRouting.targetPending)).toBeInTheDocument();
    expect(screen.getByText("$default")).toBeInTheDocument();
    expect(
      screen.getByText(HTTP_API_CONNECTION.review!.api!.functionReference)
    ).toBeInTheDocument();
    expect(screen.queryByText(/ A ALIAS /)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    expect(onAttach).toHaveBeenCalledOnce();
  });
  it.each(["disappeared", "kind"])(
    "refuses a retained review after selected source %s change",
    (condition) => {
      const onAttach = vi.fn(),
        onPrepare = vi.fn();
      const targets =
        condition === "disappeared"
          ? []
          : [{ ...HTTP_API_CONNECTION.targets[0], kind: "DEPLOYMENT" as const }];
      render(
        wrap(
          <DomainAppConnectionSheet
            {...HTTP_API_CONNECTION}
            initialStep={2}
            targets={targets}
            canAttach
            canPrepare
            onAttach={onAttach}
            onPrepare={onPrepare}
          />
        )
      );
      expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeDisabled();
      expect(screen.getByRole("button", { name: en.domainAppRouting.prepare })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
      expect(onAttach).not.toHaveBeenCalled();
      expect(onPrepare).not.toHaveBeenCalled();
    }
  );
  it.each(["unavailable", "absent-api", "empty-api", "pending-deployment", "invented-record"])(
    "refuses %s proposal despite a supplied action hint",
    (condition) => {
      const onAttach = vi.fn();
      const review = { ...HTTP_API_CONNECTION.review! };
      if (condition === "unavailable") review.recordState = "unavailable";
      if (condition === "absent-api") review.api = null;
      if (condition === "empty-api") review.api = { ...review.api!, id: "" };
      if (condition === "pending-deployment")
        review.target = { ...review.target, kind: "DEPLOYMENT" };
      if (condition === "invented-record")
        review.records = ["sample.example. A ALIAS guessed-target.example"];
      render(
        wrap(
          <DomainAppConnectionSheet
            {...HTTP_API_CONNECTION}
            initialStep={2}
            review={review}
            canAttach
            onAttach={onAttach}
          />
        )
      );
      expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeDisabled();
      expect(screen.queryByText(/guessed-target/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
      expect(onAttach).not.toHaveBeenCalled();
    }
  );
});
