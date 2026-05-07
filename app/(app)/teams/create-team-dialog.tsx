"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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
import { Textarea } from "@/components/ui/textarea";
import { CREATE_TEAM } from "@/graphql/identity/identity.mutations";
import {
  LIST_ORGANIZATIONS,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftOrganization,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

export function CreateTeamDialog({ open, onOpenChange }: Props) {
  const orgs = useQuery<{ astroliftOrganizations: AstroliftOrganization[] }>(
    LIST_ORGANIZATIONS,
  );
  // Single-tenant install: there's exactly one organization. Auto-bind
  // so users never have to think about a multi-org dropdown.
  const org = orgs.data?.astroliftOrganizations[0];

  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setSlug("");
      setDescription("");
      setSlugTouched(false);
    }
  }, [open]);

  const [createTeam, { loading }] = useMutation<{
    createTeam: MutationResult<AstroliftTeam>;
  }>(CREATE_TEAM, {
    refetchQueries: [{ query: LIST_TEAMS }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!org) return;
    const finalSlug = slug || slugify(name);
    const { data } = await createTeam({
      variables: {
        input: {
          organizationId: org.id,
          name: name.trim(),
          slug: finalSlug,
          description: description.trim() || null,
        },
      },
    });
    const result = data?.createTeam;
    if (result?.ok) {
      toast.success(`Team ${finalSlug} created`);
      onOpenChange(false);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Create failed");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>New team</SheetTitle>
          <SheetDescription>
            Teams scope projects, members, and tokens. Slugs are unique
            and reclaimable after soft-delete.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="name">Display name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
              }}
              placeholder="Backend"
              autoFocus
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="slug">Slug</Label>
            <Input
              id="slug"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugTouched(true);
              }}
              placeholder="backend"
              pattern="[a-z0-9-]+"
              required
            />
            <p className="text-muted-foreground text-xs">
              Lowercase letters, numbers, hyphens. Used in URLs.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description (optional)</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this team own?"
              rows={3}
            />
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading || !name || !org}>
              {loading ? "Creating…" : "Create team"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
