"use client";

import { GithubIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import type { AddClientIdData } from "./use-source-providers";

export type AddClientIdSheetProps = AddClientIdData & {
  /** Open the sheet by setting this to the connection being edited;
   *  pass null to close. */
  connection: AstroliftSourceConnection | null;
  onClose: () => void;
};

// GitHub App Client ID validation — mirrors the backend regex in
// astrolift_scm/schema/mutations.py so paste errors get surfaced
// before the round-trip.
const CLIENT_ID_NEW = /^Iv\d+[A-Za-z0-9]+$/;
const CLIENT_ID_LEGACY = /^[a-f0-9]{20}$/;

function looksLikeClientId(value: string): boolean {
  const v = value.trim();
  return CLIENT_ID_NEW.test(v) || CLIENT_ID_LEGACY.test(v);
}

/**
 * Focused sheet for adding a missing GitHub App Client ID to an
 * existing SourceConnection (#525 recovery flow).
 *
 * The full ConnectSourceDialog asks for kind / secret / scopes — none
 * of which are mutable here. This sheet takes the Client ID alone
 * and runs the UpdateSourceConnection mutation in place. After save
 * the connection's ``needsClientId`` flips false and the "Connect my
 * GitHub" button re-appears.
 */
export function AddClientIdSheet({ connection, onClose, saving, save }: AddClientIdSheetProps) {
  const t = useTranslations("sourceProviders.clientId");
  const tConnect = useTranslations("sourceProviders.connect");
  const [clientId, setClientId] = React.useState("");

  React.useEffect(() => {
    setClientId(connection?.appClientId ?? "");
  }, [connection]);

  const value = clientId.trim();
  const looksOk = value === "" || looksLikeClientId(value);
  const canSubmit = value !== "" && looksOk && !saving;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!connection || !canSubmit) return;
    if (await save(connection, value)) onClose();
  }

  return (
    <Sheet open={connection !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <GithubIcon className="size-4" />
            {t("title")}
          </SheetTitle>
          <SheetDescription>
            {t.rich("description", { code: (chunks) => <code>{chunks}</code> })}
          </SheetDescription>
        </SheetHeader>

        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          {connection && (
            <DefinitionList
              className="text-xs"
              items={[
                { term: t("connection"), description: connection.name },
                ...(connection.accountLogin
                  ? [
                      {
                        term: t("account"),
                        description: <code className="font-mono">{connection.accountLogin}</code>,
                      },
                    ]
                  : []),
                ...(connection.oauthClientId
                  ? [
                      {
                        term: t("existingAppId"),
                        description: <code className="font-mono">{connection.oauthClientId}</code>,
                      },
                    ]
                  : []),
              ]}
            />
          )}

          <div className="space-y-2">
            <Label htmlFor="client-id-input">{t("label")}</Label>
            <Input
              id="client-id-input"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="Iv23lic8662KXwe4XKEI"
              className="font-mono text-xs"
              autoComplete="off"
              autoFocus
              required
            />
            <p className="text-muted-foreground text-xs">
              {t.rich("help", {
                link: (chunks) => (
                  <a
                    href="https://github.com/settings/apps"
                    target="_blank"
                    rel="noreferrer noopener"
                    className="underline"
                  >
                    {chunks}
                  </a>
                ),
                em: (chunks) => <em>{chunks}</em>,
              })}
            </p>
            {value !== "" && !looksOk && (
              <p className="text-destructive text-xs">
                {tConnect.rich("appClientIdInvalid", { code: (chunks) => <code>{chunks}</code> })}
              </p>
            )}
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={onClose}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {saving ? t("saving") : t("save")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
