"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Loader2Icon, PenLineIcon, SendIcon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Separator } from "@/components/ui/separator";
import { StatTile } from "@/components/ui/stat-tile";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";
import {
  useArchiveForm,
  useFormDefinition,
  useFormSubmissions,
  usePublishForm,
} from "@/graphql/forms/forms.hooks";

import { formStatusBadgeProps } from "../status-badge";
import { FormPreviewTab } from "./preview-tab";
import { FormSubmissionsTab } from "./submissions-tab";

type Tab = "overview" | "preview" | "submissions";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "preview", label: "Preview" },
  { key: "submissions", label: "Submissions" },
];

export default function FormDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const [tab, setTab] = useState<Tab>("overview");
  const { form, loading, error } = useFormDefinition(slug);
  const { submissions, loading: subsLoading } = useFormSubmissions(slug);
  const [publishForm] = usePublishForm();
  const [archiveForm] = useArchiveForm();

  if (loading && !form) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (error || !form) {
    return (
      <div className="flex flex-1 flex-col gap-6 p-6">
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
          {error ? `Error: ${error.message}` : `No published form found for "${slug}"`}
        </div>
      </div>
    );
  }

  const handlePublish = async () => {
    const { data } = await publishForm({ variables: { slug } });
    if (data?.publishForm?.ok) {
      toast.success("Form published");
    } else {
      toast.error("Failed to publish");
    }
  };

  const handleArchive = async () => {
    const { data } = await archiveForm({ variables: { slug } });
    if (data?.archiveForm?.ok) {
      toast.success("Form archived");
    } else {
      toast.error("Failed to archive");
    }
  };

  const schemaProperties = (form.schema as Record<string, unknown>)?.properties as
    | Record<string, unknown>
    | undefined;
  const fieldCount = schemaProperties ? Object.keys(schemaProperties).length : 0;

  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold">{form.name}</h1>
            <Badge {...formStatusBadgeProps(form.status)}>{form.status}</Badge>
            <span className="text-muted-foreground text-sm">v{form.version}</span>
          </div>
          <p className="text-muted-foreground mt-1 text-sm">{form.description}</p>
        </div>
        <div className="flex gap-2">
          {form.status === "published" && (
            <Button asChild>
              <Link href={`/forms/${slug}/submit`}>
                <PenLineIcon className="mr-2 h-4 w-4" /> Fill Out
              </Link>
            </Button>
          )}
          {form.status === "draft" && (
            <Button onClick={handlePublish}>
              <SendIcon className="mr-2 h-4 w-4" /> Publish
            </Button>
          )}
          {form.status === "published" && (
            <Button variant="outline" onClick={handleArchive}>
              Archive
            </Button>
          )}
        </div>
      </div>
      <Separator />

      <nav className="border-b">
        <ul className="flex gap-1">
          {TABS.map((t) => {
            const active = tab === t.key;
            return (
              <li key={t.key}>
                <button
                  type="button"
                  onClick={() => setTab(t.key)}
                  className={cn(
                    "border-b-2 px-4 py-2 text-sm transition-colors",
                    active
                      ? "border-primary text-foreground"
                      : "text-muted-foreground hover:text-foreground border-transparent"
                  )}
                >
                  {t.label}
                  {t.key === "submissions" && form.submissionCount > 0 && (
                    <span className="bg-muted text-muted-foreground ml-2 rounded-full px-2 py-0.5 text-xs">
                      {form.submissionCount}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {tab === "overview" && <FormOverview form={form} fieldCount={fieldCount} />}
      {tab === "preview" && (
        <FormPreviewTab schema={(form.schema as Record<string, unknown>) ?? {}} />
      )}
      {tab === "submissions" && (
        <FormSubmissionsTab submissions={submissions} loading={subsLoading} />
      )}
    </div>
  );
}

function FormOverview({
  form,
  fieldCount,
}: {
  form: {
    submissionCount: number;
    publishedAt: string | null;
    schema: Record<string, unknown>;
  };
  fieldCount: number;
}) {
  const fmt = useFormatters();
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <StatTile label="Fields" value={fieldCount} />
        <StatTile label="Submissions" value={form.submissionCount} />
        <StatTile
          label="Published"
          value={form.publishedAt ? fmt.formatDate(form.publishedAt) : "Not yet"}
        />
      </div>

      <Section title="Schema">
        <pre className="bg-muted max-h-96 overflow-auto rounded-lg p-4 text-sm">
          {JSON.stringify(form.schema, null, 2)}
        </pre>
      </Section>
    </div>
  );
}
