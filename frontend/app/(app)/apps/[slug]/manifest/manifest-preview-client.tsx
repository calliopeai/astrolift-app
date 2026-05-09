"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon, FileCodeIcon } from "lucide-react";
import * as React from "react";

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
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";

interface RenderedManifest {
  appSlug: string;
  environmentName: string;
  imageTag: string;
  namespace: string;
  resources: K8sResource[];
  error: string | null;
  errorPath: string | null;
  errorLine: number | null;
  errorColumn: number | null;
}

interface K8sResource {
  apiVersion: string;
  kind: string;
  metadata: { name: string };
}

interface ManifestResp {
  astroliftRenderedManifest: RenderedManifest | null;
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

const PREVIEW_SENTINEL = "__preview__";

export function ManifestPreviewClient({ slug }: { slug: string }) {
  const [envName, setEnvName] = React.useState<string>(PREVIEW_SENTINEL);
  const [imageTag, setImageTag] = React.useState<string>("");

  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });

  const { data, loading } = useQuery<ManifestResp>(GET_RENDERED_MANIFEST, {
    variables: {
      appSlug: slug,
      environmentName: envName === PREVIEW_SENTINEL ? null : envName,
      imageTag: imageTag.trim() || null,
    },
  });

  const result = data?.astroliftRenderedManifest;

  return (
    <PageShell
      title="Manifest preview"
      description={`Renders the stored TOML for ${slug} into the Kubernetes resources the deploy activity would apply. The view is purely a preview — no cluster traffic happens here.`}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label className="text-xs uppercase tracking-wide">Environment</Label>
          <Select value={envName} onValueChange={setEnvName}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={PREVIEW_SENTINEL}>
                preview (no env required)
              </SelectItem>
              {(envs.data?.astroliftEnvironments ?? []).map((e) => (
                <SelectItem key={e.id} value={e.name}>
                  {e.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label
            className="text-xs uppercase tracking-wide"
            htmlFor="manifest-image-tag"
          >
            Image tag (optional)
          </Label>
          <Input
            id="manifest-image-tag"
            placeholder="defaults to 'preview'"
            value={imageTag}
            onChange={(e) => setImageTag(e.target.value)}
          />
        </div>
      </div>

      {loading && !result ? (
        <Skeleton className="h-64 w-full" />
      ) : !result ? (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            App not found.
          </CardContent>
        </Card>
      ) : result.error ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-destructive">
              <AlertTriangleIcon className="size-5" />
              Manifest could not be rendered
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <pre className="bg-muted overflow-auto rounded-md p-3 text-xs">
              {result.error}
            </pre>
            {result.errorPath && (
              <p className="text-muted-foreground text-xs">
                offending key:{" "}
                <span className="font-mono">{result.errorPath}</span>
                {result.errorLine && (
                  <>
                    {" "}— line{" "}
                    <span className="font-mono">{result.errorLine}</span>
                    {result.errorColumn && (
                      <>
                        , col{" "}
                        <span className="font-mono">{result.errorColumn}</span>
                      </>
                    )}
                  </>
                )}
              </p>
            )}
            <p className="text-muted-foreground text-xs">
              Fix the TOML in your repo and push; the next sync will
              re-parse.
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="secondary">
              env <span className="font-mono ml-1">{result.environmentName}</span>
            </Badge>
            <Badge variant="secondary">
              ns <span className="font-mono ml-1">{result.namespace}</span>
            </Badge>
            <Badge variant="secondary">
              image tag <span className="font-mono ml-1">{result.imageTag}</span>
            </Badge>
            <Badge variant="outline">
              {result.resources.length} resource
              {result.resources.length === 1 ? "" : "s"}
            </Badge>
          </div>

          {result.resources.length === 0 ? (
            <Card>
              <CardContent className="p-6">
                <p className="text-muted-foreground text-sm">
                  No resources rendered. Either the manifest is empty or
                  every workload is of an unsupported kind.
                </p>
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
                      <Badge
                        variant="outline"
                        className="ml-auto font-mono text-xs"
                      >
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
