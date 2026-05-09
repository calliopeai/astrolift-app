"use client";

import { useMutation } from "@apollo/client/react";
import { CopyIcon, MailIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { CREATE_INVITATION } from "@/graphql/identity/identity.mutations";
import { LIST_INVITATIONS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftRole,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roles: AstroliftRole[];
}

interface InvitationCreated {
  invitation: { id: string; email: string };
  plaintextToken: string;
  acceptUrlPath: string;
}

export function InviteDialog({ open, onOpenChange, roles }: Props) {
  const [email, setEmail] = React.useState("");
  const [roleSlug, setRoleSlug] = React.useState<string>("");
  const [expiresInDays, setExpiresInDays] = React.useState<string>("7");

  // After a successful create the dialog flips to a "share this link"
  // view. The plaintext token is the only chance — we render it once
  // with a Copy button and surface the accept URL the recipient
  // clicks.
  const [created, setCreated] = React.useState<InvitationCreated | null>(null);

  const [createInvite, { loading }] = useMutation<{
    createInvitation: MutationResult<InvitationCreated>;
  }>(CREATE_INVITATION, {
    refetchQueries: [{ query: LIST_INVITATIONS }],
    awaitRefetchQueries: true,
  });

  React.useEffect(() => {
    if (!open) {
      setEmail("");
      setRoleSlug("");
      setExpiresInDays("7");
      setCreated(null);
    }
  }, [open]);

  const orgRoles = roles.filter((r) => r.scopeLevel === "ORG");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) {
      toast.error("Email is required");
      return;
    }
    const days = parseInt(expiresInDays, 10);
    const { data } = await createInvite({
      variables: {
        input: {
          email: email.trim(),
          roleSlug: roleSlug || null,
          expiresInDays: Number.isFinite(days) && days > 0 ? days : null,
        },
      },
    });
    const result = data?.createInvitation;
    if (result?.ok && result.data) {
      toast.success("Invitation created — share the link below");
      setCreated(result.data);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Invite failed");
    }
  }

  function copyAcceptUrl() {
    if (!created) return;
    const url = `${window.location.origin}${created.acceptUrlPath}`;
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success("Accept link copied"))
      .catch(() => toast.error("Copy failed — select and copy manually"));
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Invite member</SheetTitle>
          <SheetDescription>
            Issues a one-time, hashed-at-rest invitation token. The
            plaintext is shown exactly once on this screen — share it
            with the recipient through your usual channel.
          </SheetDescription>
        </SheetHeader>
        {created ? (
          <div className="space-y-4 px-4 py-6">
            <div className="bg-amber-100 border border-amber-300 rounded-md p-3 text-sm dark:bg-amber-950/40 dark:border-amber-900/60">
              <strong className="block mb-1">Save this link now</strong>
              The token is hashed in the database — once you close this
              dialog there is no way to recover the plaintext.
            </div>
            <div className="space-y-1">
              <Label className="text-xs uppercase tracking-wide">
                Recipient
              </Label>
              <div className="font-mono text-sm">{created.invitation.email}</div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs uppercase tracking-wide">
                Accept link
              </Label>
              <div className="bg-muted flex items-center gap-2 rounded-md p-2">
                <code className="font-mono text-xs break-all flex-1">
                  {`${typeof window !== "undefined" ? window.location.origin : ""}${created.acceptUrlPath}`}
                </code>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={copyAcceptUrl}
                  className="shrink-0"
                >
                  <CopyIcon className="size-3" /> Copy
                </Button>
              </div>
            </div>
            <SheetFooter>
              <Button onClick={() => onOpenChange(false)}>Done</Button>
            </SheetFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 px-4 py-6">
            <div className="space-y-1">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                placeholder="newmember@yourcorp.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
              />
              <p className="text-muted-foreground text-xs">
                Acceptance requires the user&apos;s account email to match.
              </p>
            </div>
            <div className="space-y-1">
              <Label>Role (optional)</Label>
              <Select value={roleSlug} onValueChange={setRoleSlug}>
                <SelectTrigger>
                  <SelectValue placeholder="No role — accept-only" />
                </SelectTrigger>
                <SelectContent>
                  {orgRoles.map((r) => (
                    <SelectItem key={r.id} value={r.slug}>
                      {r.name} ({r.slug})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                If chosen, a RoleBinding is created when the invite is
                accepted. Otherwise add bindings via Grant role.
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="invite-expires">Expires in (days)</Label>
              <Input
                id="invite-expires"
                type="number"
                min={1}
                max={90}
                value={expiresInDays}
                onChange={(e) => setExpiresInDays(e.target.value)}
              />
            </div>
            <SheetFooter>
              <Button type="submit" disabled={loading}>
                <MailIcon className="size-4" /> Issue invitation
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}
