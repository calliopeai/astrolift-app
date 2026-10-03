"use client";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useDebounce } from "@/hooks/use-debounce";
import { useLocalListState, type ListDefinition } from "@/components/list/use-list-state";
import {
  LIST_LOCAL_MODEL_ARTIFACTS,
  BEGIN_LOCAL_MODEL_ARTIFACT,
  AUTHORIZE_LOCAL_MODEL_UPLOADS,
  FINALIZE_LOCAL_MODEL_ARTIFACT,
} from "@/graphql/models/local-artifacts.queries";
import type {
  ListLocalModelArtifactsQuery,
  ListLocalModelArtifactsQueryVariables,
  BeginLocalModelArtifactMutation,
  BeginLocalModelArtifactMutationVariables,
  AuthorizeLocalModelUploadsMutation,
  AuthorizeLocalModelUploadsMutationVariables,
  FinalizeLocalModelArtifactMutation,
  FinalizeLocalModelArtifactMutationVariables,
} from "@/graphql/__generated__/operations";
import { hashLocalModelFiles, validateLocalFiles } from "./hash-local-model-files";
import type { LocalImportPhase, LocalModelImportProps } from "./LocalModelImportPanel";
class ImportRefusal extends Error {}
export function useLocalModelImport(
  scopeKey: string,
  allowed: boolean | null,
  onUseArtifact: LocalModelImportProps["onUseArtifact"]
) {
  const t = useTranslations("models.shared.localImport"),
    hosting = useTranslations("models.shared.hosting"),
    client = useApolloClient(),
    { org } = useActiveOrg();
  const definition = useMemo<ListDefinition>(
    () => ({
      id: "models.local-artifacts",
      fields: [],
      searchPlaceholder: t("search"),
      defaultSort: [],
      views: [{ key: "all", label: t("all"), filters: {} }],
      paging: "cursor",
      pageSizes: [10, 25, 50],
      defaultPageSize: 25,
    }),
    [t]
  );
  const list = useLocalListState(definition),
    search = useDebounce(list.state.q, 300);
  const query = useQuery<ListLocalModelArtifactsQuery, ListLocalModelArtifactsQueryVariables>(
    LIST_LOCAL_MODEL_ARTIFACTS,
    {
      variables: {
        search: search || null,
        limit: list.state.pageSize,
        after: list.state.after ?? null,
      },
      skip: allowed !== true,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const [phase, setPhase] = useState<LocalImportPhase>({ kind: "idle" });
  const mounted = useRef(false),
    latest = useRef({ scopeKey, allowed }),
    flight = useRef<AbortController | null>(null);
  useLayoutEffect(() => {
    mounted.current = true;
    latest.current = { scopeKey, allowed };
    return () => {
      mounted.current = false;
      flight.current?.abort();
      flight.current = null;
    };
  }, [scopeKey, allowed]);
  const [phaseScope, setPhaseScope] = useState(scopeKey);
  if (phaseScope !== scopeKey) {
    setPhaseScope(scopeKey);
    setPhase({ kind: "idle" });
  }
  const selection = useRef({
    rows: query.data?.astroliftLocalModelArtifactsPage.items ?? [],
    ready: !query.loading && !query.error,
    verified: phase.kind === "verified" ? phase.artifact : null,
  });
  useLayoutEffect(() => {
    selection.current = {
      rows: query.data?.astroliftLocalModelArtifactsPage.items ?? [],
      ready: !query.loading && !query.error,
      verified: phase.kind === "verified" ? phase.artifact : null,
    };
  });
  const current = () =>
    mounted.current && latest.current.scopeKey === scopeKey && latest.current.allowed === true;
  const props: LocalModelImportProps = {
    scopeKey,
    allowed: allowed === true,
    phase,
    page: {
      list,
      rows: allowed === true ? (query.data?.astroliftLocalModelArtifactsPage.items ?? []) : [],
      loading: allowed === null || (allowed === true && query.loading && !query.data),
      stale: allowed === true && query.loading && Boolean(query.data),
      error:
        allowed !== true
          ? { message: hosting(allowed === null ? "adminChecking" : "adminRequired") }
          : query.error
            ? { message: query.error.message }
            : null,
      nextCursor: query.data?.astroliftLocalModelArtifactsPage.nextCursor ?? null,
      totalCount: null,
      onRetry: () => {
        if (current()) void query.refetch().catch(() => {});
      },
    },
    onCancel: () => {
      flight.current?.abort();
      flight.current = null;
      if (current()) setPhase({ kind: "canceled" });
    },
    onUseArtifact: (artifact) => {
      const match = (row: typeof artifact) =>
        row.id === artifact.id &&
        row.version === artifact.version &&
        row.manifestSha256 === artifact.manifestSha256 &&
        row.state === "verified";
      const observation = selection.current;
      if (
        ((observation.verified && match(observation.verified)) ||
          (observation.ready && observation.rows.some(match))) &&
        current() &&
        artifact.state === "verified" &&
        /^[a-f0-9]{64}$/.test(artifact.manifestSha256) &&
        Number.isSafeInteger(artifact.version) &&
        artifact.version > 0
      )
        onUseArtifact(artifact);
    },
    onImport: async (name, files) => {
      if (!current() || flight.current || !org?.id) return;
      const invalid = validateLocalFiles(files);
      if (invalid) {
        setPhase({
          kind: "failed",
          message: t(invalid === "files" ? "invalidFiles" : "invalidSizes"),
        });
        return;
      }
      const controller = new AbortController();
      flight.current = controller;
      const active = () => current() && flight.current === controller && !controller.signal.aborted;
      const check = <T extends { ok: boolean; errors: { message: string }[]; data?: unknown }>(
        envelope: T | undefined | null
      ) => {
        if (!envelope?.ok || envelope.errors.length)
          throw new ImportRefusal(envelope?.errors[0]?.message ?? t("failed"));
        return envelope;
      };
      try {
        setPhase({ kind: "hashing", name: files[0].name, percent: 0 });
        const manifest = await hashLocalModelFiles(files, controller.signal, (file, percent) => {
          if (active()) setPhase({ kind: "hashing", name: file, percent });
        });
        if (!active()) return;
        const begun = check(
          (
            await client.mutate<
              BeginLocalModelArtifactMutation,
              BeginLocalModelArtifactMutationVariables
            >({
              mutation: BEGIN_LOCAL_MODEL_ARTIFACT,
              variables: { input: { organizationId: org.id, name: name.trim(), files: manifest } },
              fetchPolicy: "no-cache",
            })
          ).data?.beginLocalModelArtifact
        );
        if (!active()) return;
        if (!begun.data) {
          setPhase({ kind: "failed", message: t("failed") });
          return;
        }
        const artifact = begun.data;
        if (
          !artifact.id ||
          artifact.state !== "uploading" ||
          !/^[a-f0-9]{64}$/.test(artifact.manifestSha256) ||
          artifact.fileCount !== files.length ||
          artifact.sizeBytes !== String(files.reduce((sum, file) => sum + file.size, 0)) ||
          !Number.isSafeInteger(artifact.version) ||
          artifact.version < 1
        )
          throw new ImportRefusal(t("failed"));
        for (const [index, file] of files.entries()) {
          if (!active()) return;
          setPhase({ kind: "uploading", name: file.name, index: index + 1, count: files.length });
          // Issue current grants before each upload so later files do not reuse
          // capabilities that expired while an earlier large file was streamed.
          const authorization = check(
            (
              await client.mutate<
                AuthorizeLocalModelUploadsMutation,
                AuthorizeLocalModelUploadsMutationVariables
              >({
                mutation: AUTHORIZE_LOCAL_MODEL_UPLOADS,
                variables: { input: { id: artifact.id, expectedVersion: artifact.version } },
                fetchPolicy: "no-cache",
              })
            ).data?.authorizeLocalModelUploads
          ).data;
          if (!active()) return;
          if (
            !authorization ||
            authorization.artifact.id !== artifact.id ||
            authorization.artifact.version !== artifact.version ||
            authorization.artifact.manifestSha256 !== artifact.manifestSha256 ||
            authorization.artifact.state !== "uploading"
          )
            throw new ImportRefusal(t("failed"));
          const grant = authorization.files.find(
            (row) => row.name === file.name && row.sizeBytes === String(file.size)
          );
          if (!grant || new URL(grant.uploadUrl).protocol !== "https:")
            throw new ImportRefusal(t("failed"));
          const headers = new Headers(
            grant.headers.map((header) => [header.name, header.value] as [string, string])
          );
          const uploaded = await fetch(grant.uploadUrl, {
            method: "PUT",
            body: file,
            headers,
            signal: controller.signal,
            credentials: "omit",
            referrerPolicy: "no-referrer",
            redirect: "error",
            cache: "no-store",
          });
          if (!active()) return;
          if (!uploaded.ok) throw new ImportRefusal(t("failed"));
        }
        if (!active()) return;
        setPhase({ kind: "verifying" });
        const finalized = check(
          (
            await client.mutate<
              FinalizeLocalModelArtifactMutation,
              FinalizeLocalModelArtifactMutationVariables
            >({
              mutation: FINALIZE_LOCAL_MODEL_ARTIFACT,
              variables: { input: { id: artifact.id, expectedVersion: artifact.version } },
              fetchPolicy: "no-cache",
            })
          ).data?.finalizeLocalModelArtifact
        );
        if (!active()) return;
        const verified = finalized.data;
        if (!verified) {
          setPhase({ kind: "accepted" });
          return;
        }
        if (
          verified.id !== artifact.id ||
          verified.state !== "verified" ||
          verified.manifestSha256 !== artifact.manifestSha256 ||
          verified.fileCount !== artifact.fileCount ||
          verified.sizeBytes !== artifact.sizeBytes ||
          !Number.isSafeInteger(verified.version) ||
          verified.version < artifact.version
        )
          throw new ImportRefusal(t("failed"));
        setPhase({ kind: "verified", artifact: verified, refreshFailed: false });
        let refreshFailed = false;
        try {
          await query.refetch();
        } catch {
          refreshFailed = true;
        }
        if (active()) setPhase({ kind: "verified", artifact: verified, refreshFailed });
      } catch (error) {
        if (active())
          setPhase({
            kind: "failed",
            message:
              error instanceof ImportRefusal
                ? error.message
                : error instanceof Error && error.message === "HASH_UNSUPPORTED"
                  ? t("browserUnsupported")
                  : t("failed"),
          });
      } finally {
        if (flight.current === controller) flight.current = null;
      }
    },
  };
  return props;
}
