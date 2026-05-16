"use client";

import { FileTextIcon } from "lucide-react";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { ManifestSchema } from "./types";

interface Props {
  schema: ManifestSchema;
}

export function ManifestClient({ schema }: Props) {
  const [query, setQuery] = React.useState("");

  const sections = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return schema.sections;
    return schema.sections.filter((section) => {
      const haystack = [
        section.key,
        section.title,
        section.summary,
        ...section.fields.map((f) => `${f.name} ${f.description}`),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [query, schema.sections]);

  return (
    <PageShell
      title="Manifest reference"
      description="Schema for astrolift.toml — the per-app manifest read by the CLI and the platform's registration mutation."
      actions={
        <Badge variant="outline" className="font-mono text-xs">
          schema v{schema.version}
        </Badge>
      }
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <FileTextIcon className="size-4" />
            Top-level sections
          </CardTitle>
          <CardDescription>
            Each section in astrolift.toml maps to a backend dataclass. The
            full per-field reference (defaults, validation, examples) ships
            with the docs pull from astrolift-docs.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter sections — name, field, description…"
            aria-label="Filter manifest sections"
            className="max-w-md"
          />
          <p className="text-muted-foreground mt-2 text-xs">
            {sections.length === schema.sections.length
              ? `${schema.sections.length} sections`
              : `${sections.length} of ${schema.sections.length} sections`}
          </p>
        </CardContent>
      </Card>

      {sections.map((section) => (
        <Card key={section.key}>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="font-mono text-base">
                {section.title}
              </CardTitle>
              {section.required ? (
                <Badge className="text-[10px] uppercase tracking-wide">
                  required
                </Badge>
              ) : (
                <Badge variant="outline" className="text-[10px] uppercase">
                  optional
                </Badge>
              )}
            </div>
            <CardDescription>{section.summary}</CardDescription>
          </CardHeader>
          {section.fields.length > 0 && (
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[180px]">Field</TableHead>
                    <TableHead className="w-[140px]">Type</TableHead>
                    <TableHead className="w-[80px]">Required</TableHead>
                    <TableHead>Description</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {section.fields.map((field) => (
                    <TableRow key={field.name}>
                      <TableCell className="font-mono text-xs">
                        {field.name}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {field.type}
                      </TableCell>
                      <TableCell>
                        {field.required ? (
                          <Badge className="text-[10px]">yes</Badge>
                        ) : (
                          <span className="text-muted-foreground text-xs">
                            no
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground whitespace-normal text-xs leading-relaxed">
                        {field.description}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          )}
        </Card>
      ))}
    </PageShell>
  );
}
