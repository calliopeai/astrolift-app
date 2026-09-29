"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import type { AstroliftSshDeployKeyCreated } from "@/graphql/scm/scm.types";

import type { GenerateSshKeyData } from "./use-source-providers";

export type GenerateSshKeySheetProps = GenerateSshKeyData & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Stories only: open straight onto the generated-key step. */
  initialKey?: AstroliftSshDeployKeyCreated["key"] | null;
};

export function GenerateSshKeySheet({
  open,
  onOpenChange,
  generating,
  generate,
  initialKey = null,
}: GenerateSshKeySheetProps) {
  const [name, setName] = React.useState("");
  const [appSlug, setAppSlug] = React.useState("");
  const [pasted, setPasted] = React.useState<AstroliftSshDeployKeyCreated["key"] | null>(
    initialKey
  );

  React.useEffect(() => {
    if (!open) {
      setName("");
      setAppSlug("");
      setPasted(null);
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const key = await generate(name.trim(), appSlug.trim() || null);
    if (key) setPasted(key);
  }

  async function copyPublicKey(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Public key copied");
    } catch {
      toast.error("Couldn't copy — select text and copy manually");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Generate SSH deploy key</SheetTitle>
          <SheetDescription>
            ed25519 keypair. Public key is shown for you to paste into the repo&apos;s deploy-key
            settings; the private key stays encrypted in the platform secrets backend and is never
            shown.
          </SheetDescription>
        </SheetHeader>
        {pasted ? (
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
            <div className="space-y-2">
              <Label>Fingerprint</Label>
              <div className="font-mono text-xs">{pasted.fingerprintSha256}</div>
            </div>
            <div className="space-y-2">
              <Label>Public key (paste into the repo&apos;s deploy keys)</Label>
              <textarea
                readOnly
                rows={4}
                value={pasted.publicKey}
                className="bg-muted text-2xs rounded-md border p-2 font-mono"
                onClick={(e) => (e.target as HTMLTextAreaElement).select()}
              />
              <Button size="sm" variant="outline" onClick={() => copyPublicKey(pasted.publicKey)}>
                Copy public key
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              GitHub: <code>Settings → Deploy keys → Add deploy key</code>. Tick &ldquo;Allow write
              access&rdquo; only if Astrolift needs to push (e.g. for tag-based releases). GitLab:
              <code> Settings → Repository → Deploy Keys</code>.
            </p>
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </SheetFooter>
          </div>
        ) : (
          <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="prod-deploy-key"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="app-slug">App slug (optional)</Label>
              <Input
                id="app-slug"
                value={appSlug}
                onChange={(e) => setAppSlug(e.target.value)}
                placeholder="leave blank for org-scoped key"
                className="font-mono text-xs"
              />
              <p className="text-muted-foreground text-xs">
                Leave blank for an org-scoped key (reusable across every app). Set an app slug for a
                per-app key — tighter blast radius if a single repo is compromised.
              </p>
            </div>
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={generating || !name.trim()}>
                {generating ? "Generating…" : "Generate"}
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}
