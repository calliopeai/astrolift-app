"use client";

import { QueryError } from "@/components/QueryError";

import { AlertTriangleIcon, FileCodeIcon } from "lucide-react";
import type * as React from "react";
import { useTranslations } from "next-intl";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import { PREVIEW_SENTINEL, type useManifestPreview } from "./use-manifest-preview";

export type ManifestPreviewScreenProps = ReturnType<typeof useManifestPreview> & {
  slug: string;
  /** The app tab bar. */
  tabs?: React.ReactNode;
};

/** Renders the stored TOML into the Kubernetes resources a deploy would apply. */
export function ManifestPreviewScreen({
  slug,
  tabs,
  envName,
  setEnvName,
  imageTag,
  setImageTag,
  environments,
  loading,
  error,
  onRetry,
  environmentsLoading,
  environmentsError,
  onRetryEnvironments,
  result,
}: ManifestPreviewScreenProps) {
  const t = useTranslations("apps.config.preview");
  return (
    <PageShell
      title={t("title")}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug })}
        </span>
      }
    >
      {tabs}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label className="text-xs tracking-wide uppercase">{t("environment")}</Label>
          <Select
            value={envName}
            onValueChange={setEnvName}
            disabled={environmentsLoading || Boolean(environmentsError)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={PREVIEW_SENTINEL}>{t("defaultEnvironment")}</SelectItem>
              {environments.map((e) => (
                <SelectItem key={e.id} value={e.name}>
                  {e.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="text-xs tracking-wide uppercase" htmlFor="manifest-image-tag">
            {t("imageTag")}
          </Label>
          <Input
            id="manifest-image-tag"
            placeholder={t("imagePlaceholder")}
            value={imageTag}
            onChange={(e) => setImageTag(e.target.value)}
          />
        </div>
      </div>

      <QueryError
        title={t("environmentsError")}
        error={environmentsError}
        onRetry={onRetryEnvironments}
      />
      {environmentsLoading && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("loadingEnvironments")}
        </p>
      )}
      {error ? (
        <QueryError title={t("queryError")} error={error} onRetry={onRetry} />
      ) : loading && !result ? (
        <Skeleton className="h-64 w-full" />
      ) : !result ? (
        <Card>
          <CardContent className="text-muted-foreground p-6 text-sm">{t("notFound")}</CardContent>
        </Card>
      ) : result.error ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2">
              <AlertTriangleIcon className="size-5" />
              {t("renderError")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <pre className="bg-muted overflow-auto rounded-md p-3 text-xs">{result.error}</pre>
            {result.errorPath && (
              <p className="text-muted-foreground text-xs">
                {t("offendingKey")} <span className="font-mono">{result.errorPath}</span>
                {result.errorLine != null && (
                  <>
                    {" "}
                    — <span className="font-mono">{t("line", { line: result.errorLine })}</span>
                    {result.errorColumn != null && (
                      <>
                        ,{" "}
                        <span className="font-mono">
                          {t("column", { column: result.errorColumn })}
                        </span>
                      </>
                    )}
                  </>
                )}
              </p>
            )}
            <p className="text-muted-foreground text-xs">{t("fixHint")}</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="secondary">
              {t("environment")} <span className="ml-1 font-mono">{result.environmentName}</span>
            </Badge>
            <Badge variant="secondary">
              {t("namespace")} <span className="ml-1 font-mono">{result.namespace}</span>
            </Badge>
            <Badge variant="secondary">
              {t("imageBadge")} <span className="ml-1 font-mono">{result.imageTag}</span>
            </Badge>
            <Badge variant="outline">
              {t("resourceCount", { count: result.resources.length })}
            </Badge>
          </div>

          {result.resources.length === 0 ? (
            <Card>
              <CardContent className="p-6">
                <p className="text-muted-foreground text-sm">{t("noResources")}</p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-3">
              {result.resources.map((resource, i) => (
                <Card key={`${resource.kind}-${resource.metadata.name}-${i}`}>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <FileCodeIcon className="size-4" />
                      <span className="font-mono">
                        {resource.kind}
                        <span className="text-muted-foreground">
                          {" / "}
                          {resource.metadata.name}
                        </span>
                      </span>
                      <Badge variant="outline" className="ml-auto font-mono text-xs">
                        {resource.apiVersion}
                      </Badge>
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 text-xs">
                      {JSON.stringify(resource, null, 2)}
                    </pre>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}
