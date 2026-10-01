"use client";

import { useTranslations } from "next-intl";

import { Wand2Icon } from "lucide-react";
import * as React from "react";

import { SlugStatusHint } from "@/components/screens/projects/SlugStatusHint";
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
import type { AstroliftTeam } from "@/graphql/identity/identity.types";

import type { useEditTeam } from "./use-edit-team";

export type EditTeamSheetProps = ReturnType<typeof useEditTeam> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: AstroliftTeam | null;
};

export function EditTeamSheet({
  open,
  onOpenChange,
  name,
  setName,
  slug,
  setSlug,
  slugStatus,
  canSubmit,
  saving,
  generateSlug,
  save,
}: EditTeamSheetProps) {
  const copy = useTranslations("teams");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (await save()) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{copy("editTitle")}</SheetTitle>
          <SheetDescription>{copy("editDescription")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="edit-team-name">{copy("displayName")}</Label>
            <Input
              id="edit-team-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={copy("exampleName")}
              autoFocus
              required
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="edit-team-slug">{copy("slug")}</Label>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 gap-1 px-2 text-xs"
                onClick={generateSlug}
              >
                <Wand2Icon className="size-3" />
                {copy("generate")}
              </Button>
            </div>
            <Input
              id="edit-team-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="backend"
              pattern="[a-z0-9-]+"
              required
              aria-invalid={slugStatus === "invalid" || slugStatus === "taken"}
            />
            <SlugStatusHint
              status={slugStatus}
              kind="team"
              labels={{
                empty: copy("slugStatus.empty"),
                invalid: copy("slugStatus.invalid"),
                unchanged: copy("slugStatus.unchanged"),
                checking: copy("slugStatus.checking"),
                available: copy("slugStatus.available"),
                taken: copy("slugStatus.taken"),
              }}
            />
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {copy("cancel")}
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {saving ? copy("saving") : copy("save")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
