"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import type { ClusterCertificate } from "./use-app-domains";

// Validation method vocabulary surfaced to operators (#425). The
// platform also accepts an empty value (defaults to ``dns_txt``);
// the dropdown picks ``dns_txt`` as the default so the operator
// sees what they're committing to.
//
// "byo_cert" is a UX-level sentinel — not a backend
// ``ValidationMethod``. The backend models BYO as a separate
// ``CertificateState`` axis: the operator picks ``dns_txt`` to
// validate the hostname, then uploads their own cert via the
// per-domain Replace action (which fires
// ``uploadCustomDomainCertificate``). Picking "byo_cert" in the
// dropdown is a hint that surfaces the follow-up upload step;
// under the hood we still send ``dns_txt`` because that's what
// the API accepts.
const VALIDATION_METHODS = ["dns_txt", "http_01", "dns_01", "byo_cert"] as const;
type ValidationMethodChoice = (typeof VALIDATION_METHODS)[number];

const DEFAULT_VALIDATION_METHOD: ValidationMethodChoice = "dns_txt";

function backendValidationMethod(choice: ValidationMethodChoice): string {
  // BYO is not a real validation method on the backend — fall back
  // to dns_txt for the initial hostname validation. The follow-up
  // BYO cert upload lands via ``uploadCustomDomainCertificate``.
  if (choice === "byo_cert") return "dns_txt";
  return choice;
}

export interface AddDomainSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (
    hostname: string,
    validationMethod: string,
    isWildcard: boolean,
    sniCertRef: string
  ) => Promise<boolean>;
  busy: boolean;
  // #682 — wildcard toggle. Held by the hook because it gates the #858
  // cert picker query; reset there when the sheet closes.
  isWildcard: boolean;
  onWildcardChange: (isWildcard: boolean) => void;
  // #858 — the app's bound cluster provider, derived from its
  // environments. Null when no env is bound to a cluster yet → the SNI
  // field stays a free-text input.
  clusterProviderSlug: string | null;
  /** The provider-backed cert picker is eligible and the backend supports it. */
  showCertCombobox: boolean;
  certs: ClusterCertificate[];
  certsLoading: boolean;
}

export function AddDomainSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
  isWildcard,
  onWildcardChange,
  clusterProviderSlug,
  showCertCombobox,
  certs,
  certsLoading,
}: AddDomainSheetProps) {
  const t = useTranslations("apps.domains");
  const tCommon = useTranslations("apps.common");
  const [hostname, setHostname] = React.useState("");
  const [method, setMethod] = React.useState<ValidationMethodChoice>(DEFAULT_VALIDATION_METHOD);
  // #682 — optional SNI cert ref. When ``isWildcard`` flips on we force
  // ``dns_01`` (the only ACME challenge that supports wildcards) so the
  // operator sees what they're committing to.
  const [sniCertRef, setSniCertRef] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setHostname("");
      setMethod(DEFAULT_VALIDATION_METHOD);
      setSniCertRef("");
    }
  }, [open]);

  const isByo = method === "byo_cert";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("addSheet.title")}</SheetTitle>
          <SheetDescription>{t("addSheet.description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!hostname.trim()) return;
            await onSubmit(
              hostname.trim().toLowerCase(),
              backendValidationMethod(method),
              isWildcard,
              sniCertRef.trim()
            );
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="d-hostname">{t("addSheet.hostname")}</Label>
            <Input
              id="d-hostname"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder={isWildcard ? "tenant.acme.com" : "checkout.acme.com"}
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
            {isWildcard && hostname.trim() && (
              <p className="text-muted-foreground font-mono text-xs">
                Cert will cover{" "}
                <span className="text-foreground">*.{hostname.trim().toLowerCase()}</span>
              </p>
            )}
          </div>

          {/* #682 — Wildcard toggle. Sits between hostname and method
              because flipping it forces the validation method to
              dns_01 (the only ACME challenge that supports wildcards). */}
          <div className="border-border bg-muted/30 space-y-2 rounded-md border p-3">
            <label htmlFor="d-wildcard" className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                id="d-wildcard"
                type="checkbox"
                checked={isWildcard}
                onChange={(e) => {
                  const next = e.target.checked;
                  onWildcardChange(next);
                  // Wildcards only work with DNS-01. Flipping the
                  // toggle on swaps the method in lock-step so the
                  // operator sees what they're committing to.
                  if (next) setMethod("dns_01");
                }}
                className="mt-0.5"
              />
              <span className="flex-1">
                <span className="font-medium">Wildcard domain</span>
                <span className="text-muted-foreground block text-xs">
                  Cover every subdomain under <code className="font-mono">*.hostname</code>.
                  Requires DNS-01 validation.
                </span>
              </span>
            </label>
            {isWildcard && (
              <div className="space-y-2 pt-2">
                <Label htmlFor="d-sni-ref" className="text-xs">
                  SNI cert ref <span className="text-muted-foreground font-normal">(optional)</span>
                </Label>
                {showCertCombobox ? (
                  // #858 — provider-backed cert picker. The combobox's
                  // own input doubles as free entry: a chosen item sets
                  // the underlying ARN; typing a value that matches no
                  // item is still captured via onInputValueChange, so an
                  // operator can paste an ARN the list didn't surface.
                  <>
                    <Combobox
                      items={certs}
                      itemToStringLabel={(c) => {
                        const cert = c as { domainName: string; name: string };
                        return cert.domainName || cert.name;
                      }}
                      onValueChange={(v) => {
                        if (v && typeof v === "object" && "arn" in v) {
                          setSniCertRef((v as { arn: string }).arn);
                        }
                      }}
                      inputValue={sniCertRef}
                      onInputValueChange={(v) => setSniCertRef(v ?? "")}
                    >
                      <ComboboxInput
                        id="d-sni-ref"
                        placeholder={
                          certsLoading && certs.length === 0
                            ? "Loading certificates…"
                            : `Search ${certs.length} certificate${certs.length === 1 ? "" : "s"}…`
                        }
                        spellCheck={false}
                        className="font-mono text-xs"
                      />
                      <ComboboxContent>
                        <ComboboxEmpty>
                          No matching certificates — type an ARN to use it directly.
                        </ComboboxEmpty>
                        <ComboboxList>
                          {(item) => {
                            const c = item as {
                              arn: string;
                              name: string;
                              domainName: string;
                              status: string;
                            };
                            return (
                              <ComboboxItem key={c.arn} value={c}>
                                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                                  <span className="truncate text-xs">{c.domainName || c.name}</span>
                                  <span className="text-muted-foreground text-2xs truncate font-mono">
                                    {c.arn}
                                  </span>
                                </div>
                              </ComboboxItem>
                            );
                          }}
                        </ComboboxList>
                      </ComboboxContent>
                    </Combobox>
                    <p className="text-muted-foreground text-xs">
                      Pick an issued certificate from the {clusterProviderSlug?.toUpperCase()}{" "}
                      cluster, or paste a ref. Leave blank for platform-managed.
                    </p>
                  </>
                ) : (
                  <>
                    <Input
                      id="d-sni-ref"
                      value={sniCertRef}
                      onChange={(e) => setSniCertRef(e.target.value)}
                      placeholder="arn:aws:acm:… / projects/…/certificates/… / cert-name"
                      spellCheck={false}
                      className="font-mono text-xs"
                    />
                    <p className="text-muted-foreground text-xs">
                      ACM ARN, GCP cert name, Azure cert ID, or k8s Secret ref. Leave blank for
                      platform-managed.
                    </p>
                  </>
                )}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="d-method">{t("addSheet.validationMethod")}</Label>
            <Select
              value={method}
              onValueChange={(next) => setMethod(next as ValidationMethodChoice)}
              disabled={isWildcard}
            >
              <SelectTrigger id="d-method" className="font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {VALIDATION_METHODS.map((m) => (
                  <SelectItem key={m} value={m} className="font-mono text-xs">
                    {t(`addSheet.methodLabels.${m}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              {isWildcard
                ? "DNS-01 is required for wildcard certificates."
                : t(`addSheet.methodHints.${method}`)}
            </p>
            {isByo && !isWildcard && (
              <div className="border-info-border bg-info/5 rounded-md border p-2 text-xs">
                <p className="text-info-fg font-medium">{t("addSheet.byoTitle")}</p>
                <p className="text-muted-foreground">{t("addSheet.byoFollowup")}</p>
              </div>
            )}
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !hostname.trim()}>
              {busy ? t("addSheet.submitting") : t("addSheet.submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
