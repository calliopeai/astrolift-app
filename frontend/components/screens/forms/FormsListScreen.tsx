"use client";

import Link from "next/link";
import { Loader2Icon, PlusIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { FormDefinition } from "@/graphql/forms/forms.types";

import { formStatusBadgeProps } from "./form-status";

export type FormsListScreenProps = {
  forms: FormDefinition[];
  loading: boolean;
  error?: { message: string } | null;
};

/** The form definitions list: status, visibility, version and submission count per form. */
export function FormsListScreen({ forms, loading, error }: FormsListScreenProps) {
  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Forms</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Manage form definitions, view submissions, and publish forms.
          </p>
        </div>
        <Button asChild>
          <Link href="/forms/new">
            <PlusIcon className="mr-2 h-4 w-4" />
            New Form
          </Link>
        </Button>
      </div>
      <Separator />

      {loading && (
        <div className="flex items-center justify-center p-12">
          <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
        </div>
      )}

      {error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
          Error loading forms: {error.message}
        </div>
      )}

      {!loading && forms.length === 0 && (
        <div className="text-muted-foreground py-12 text-center">
          No forms yet. Create your first form to get started.
        </div>
      )}

      <div className="grid gap-4">
        {forms.map((form) => (
          <Link
            key={`${form.slug}-${form.version}`}
            href={`/forms/${form.slug}`}
            className="hover:bg-muted/50 flex items-center justify-between rounded-lg border p-4 transition-colors"
          >
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{form.name}</span>
                <Badge {...formStatusBadgeProps(form.status)}>{form.status}</Badge>
                {form.isPublic && <Badge variant="outline">Public</Badge>}
              </div>
              <span className="text-muted-foreground text-sm">{form.description || form.slug}</span>
            </div>
            <div className="text-muted-foreground flex items-center gap-4 text-sm">
              <span>v{form.version}</span>
              <span>{form.submissionCount} submissions</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
