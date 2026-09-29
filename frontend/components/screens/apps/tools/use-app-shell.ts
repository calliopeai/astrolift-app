"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { FileUploadResult } from "@/graphql/__generated__/schema";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { UPLOAD_FILE } from "@/graphql/uploads/uploads.mutations";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

export interface UploadedScript {
  name: string;
  publicUrl: string;
}

/**
 * Control › Shell data: the app, and the script upload (#673) that feeds the
 * shell. Pod + container selection comes from `usePodTarget`.
 */
export function useAppShell(slug: string) {
  const t = useTranslations("apps.shell");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp ?? null;

  // #673 — script upload. Operator picks a .py/.sh/.sql/etc. file; we mint a
  // pre-signed PUT URL via fileUpload, push the bytes to object storage, and
  // surface the public URL + a paste-ready `curl`/run pair so the next
  // `astro exec` lands the file at /tmp and runs it.
  const [uploadFile, uploadState] = useMutation<{ fileUpload: FileUploadResult }>(UPLOAD_FILE);
  const [uploadedFile, setUploadedFile] = React.useState<UploadedScript | null>(null);
  const [putInFlight, setPutInFlight] = React.useState(false);
  const uploading = uploadState.loading || putInFlight;

  const onPickScript = async (file: File) => {
    setUploadedFile(null);
    setPutInFlight(false);
    const mimetype = file.type || "text/plain";
    try {
      const res = await uploadFile({ variables: { mimetype, name: file.name } });
      const result = res.data?.fileUpload;
      const url = result?.preSignedUrl;
      const publicUrl = result?.publicUrl;
      if (!url || !publicUrl) {
        toast.error(t("upload.failed", { error: t("upload.noUrl") }));
        return;
      }
      setPutInFlight(true);
      const put = await fetch(url, {
        method: "PUT",
        body: file,
        headers: { "Content-Type": mimetype },
      });
      if (!put.ok) {
        toast.error(t("upload.failed", { error: `HTTP ${put.status}` }));
        return;
      }
      setUploadedFile({ name: file.name, publicUrl });
      toast.success(t("upload.success", { name: file.name }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t("upload.failed", { error: msg }));
    } finally {
      setPutInFlight(false);
    }
  };

  const uploadedCommand = React.useMemo(() => {
    if (!uploadedFile) return null;
    const name = uploadedFile.name;
    const lower = name.toLowerCase();
    const path = `/tmp/${name}`;
    if (lower.endsWith(".py")) return `python ${path}`;
    if (lower.endsWith(".sh")) return `bash ${path}`;
    if (lower.endsWith(".sql")) return `psql "$DATABASE_URL" < ${path}`;
    if (lower.endsWith(".rb")) return `ruby ${path}`;
    if (lower.endsWith(".js")) return `node ${path}`;
    if (lower.endsWith(".ts")) return `npx tsx ${path}`;
    if (lower.endsWith(".go")) return `go run ${path}`;
    return path;
  }, [uploadedFile]);

  const fetchCommand = React.useMemo(() => {
    if (!uploadedFile) return null;
    return `curl -fsSL -o /tmp/${uploadedFile.name} '${uploadedFile.publicUrl}'`;
  }, [uploadedFile]);

  return {
    slug,
    app: a,
    loading: app.loading && !a,
    uploading,
    uploadedFile,
    uploadedCommand,
    fetchCommand,
    onPickScript,
    onClearUpload: () => setUploadedFile(null),
  };
}
