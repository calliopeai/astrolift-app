"use client";

import { useMutation } from "@apollo/client/react";
import { AlertTriangleIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
import { UPDATE_SOURCE_CONNECTION } from "@/graphql/scm/scm.mutations";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type {
  AstroliftSourceConnection,
  MutationResult,
} from "@/graphql/scm/scm.types";

interface Props {
  /** Open the dialog by setting this to the connection being edited;
   *  pass null to close. */
  connection: AstroliftSourceConnection | null;
  onClose: () => void;
}

// GitHub App Client ID validation — mirrors the backend regex in
// astrolift_scm/schema/mutations.py so paste errors get surfaced
// before the round-trip.
const CLIENT_ID_NEW = /^Iv\d+[A-Za-z0-9]+$/;
const CLIENT_ID_LEGACY = /^[a-f0-9]{20}$/;

function looksLikeClientId(value: string): boolean {
  const v = value.trim();
  return CLIENT_ID_NEW.test(v) || CLIENT_ID_LEGACY.test(v);
}

interface UpdateResp {
  updateSourceConnection: MutationResult<{
    id: string;
    appClientId: string;
    needsClientId: boolean;
  }>;
}

/**
 * Focused dialog for adding a missing GitHub App Client ID to an
 * existing SourceConnection (#525 recovery flow).
 *
 * The full ConnectSourceDialog asks for kind / secret / scopes — none
 * of which are mutable here. This dialog takes the Client ID alone
 * and runs the UpdateSourceConnection mutation in place. After save
 * the connection's ``needsClientId`` flips false and the "Connect my
 * GitHub" button re-appears.
 */
export function AddClientIdDialog({ connection, onClose }: Props) {
  const [clientId, setClientId] = React.useState("");

  React.useEffect(() => {
    setClientId(connection?.appClientId ?? "");
  }, [connection]);

  const [update, { loading }] = useMutation<UpdateResp>(
    UPDATE_SOURCE_CONNECTION,
    {
      refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
      awaitRefetchQueries: true,
    },
  );

  const value = clientId.trim();
  const looksOk = value === "" || looksLikeClientId(value);
  const canSubmit = value !== "" && looksOk && !loading;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!connection || !canSubmit) return;
    const { data } = await update({
      variables: {
        input: {
          id: connection.id,
          appClientId: value,
        },
      },
    });
    if (data?.updateSourceConnection.ok) {
      toast.success(`Client ID saved for ${connection.name}`);
      onClose();
    } else {
      toast.error(
        data?.updateSourceConnection.errors?.[0]?.message ?? "Save failed",
      );
    }
  }

  return (
    <Sheet open={connection !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <AlertTriangleIcon className="size-4 text-warning-fg" />
            Add GitHub App Client ID
          </SheetTitle>
          <SheetDescription>
            This connection is missing the user-to-server OAuth Client ID. The
            &quot;Connect my GitHub&quot; flow will 404 at github.com until
            it&apos;s set. The Client ID is distinct from the App ID — find it
            on your GitHub App settings page (looks like{" "}
            <code>Iv23l…</code> for new GitHub Apps).
          </SheetDescription>
        </SheetHeader>

        <form
          onSubmit={submit}
          className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4"
        >
          {connection && (
            <DefinitionList
              className="text-xs"
              items={[
                { term: "Connection", description: connection.name },
                ...(connection.accountLogin
                  ? [
                      {
                        term: "Account",
                        description: (
                          <code className="font-mono">{connection.accountLogin}</code>
                        ),
                      },
                    ]
                  : []),
                ...(connection.oauthClientId
                  ? [
                      {
                        term: "App ID (existing)",
                        description: (
                          <code className="font-mono">{connection.oauthClientId}</code>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          )}

          <div className="space-y-2">
            <Label htmlFor="client-id-input">GitHub App Client ID</Label>
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
              <a
                href="https://github.com/settings/apps"
                target="_blank"
                rel="noreferrer noopener"
                className="underline"
              >
                Open GitHub App settings →
              </a>{" "}
              The Client ID is shown in the <em>About</em> section of your App
              page, separately from the numeric App ID.
            </p>
            {value !== "" && !looksOk && (
              <p className="text-destructive text-xs">
                Doesn&apos;t look like a GitHub App Client ID — expected{" "}
                <code>Iv…</code> or a 20-char hex string.
              </p>
            )}
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {loading ? "Saving…" : "Save Client ID"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
