"use client";

import { useTranslations } from "next-intl";

import * as React from "react";

import {
  slugify,
  isValidSlug,
  SLUG_MAX,
  SLUG_INPUT_PATTERN,
} from "@/components/screens/projects/project-team-slug";
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

import type { useCreateTeam } from "./use-create-team";

export type CreateTeamSheetProps = ReturnType<typeof useCreateTeam> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function CreateTeamSheet({
  open,
  onOpenChange,
  hasOrg,
  creating,
  createTeam,
}: CreateTeamSheetProps) {
  const copy = useTranslations("teams");
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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (creating || !hasOrg || !name.trim() || !isValidSlug(slug)) return;
    if (await createTeam({ name, slug, description })) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{copy("newTeam")}</SheetTitle>
          <SheetDescription>{copy("createDescription")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="name">{copy("displayName")}</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
              }}
              placeholder={copy("exampleName")}
              autoFocus
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="slug">{copy("slug")}</Label>
            <Input
              id="slug"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugTouched(true);
              }}
              placeholder="backend"
              pattern={SLUG_INPUT_PATTERN}
              maxLength={SLUG_MAX}
              aria-invalid={slug.length > 0 && !isValidSlug(slug)}
              required
            />
            <p className="text-muted-foreground text-xs">{copy("slugHint")}</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">{copy("descriptionOptional")}</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={copy("descriptionPlaceholder")}
              rows={3}
            />
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {copy("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={creating || !name.trim() || !hasOrg || !isValidSlug(slug)}
            >
              {creating ? copy("creating") : copy("create")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
