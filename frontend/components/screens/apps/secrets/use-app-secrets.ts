"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useListState, useLocalListState } from "@/components/list/use-list-state";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import {
  BULK_IMPORT_APP_SECRETS,
  DELETE_APP_SECRET,
  DETACH_SECRET_BUNDLE,
  REVEAL_APP_SECRET,
  ROTATE_APP_SECRET,
  SET_APP_SECRET,
} from "@/graphql/services/services.mutations";
import {
  GET_APP_VERSION,
  LIST_APP_SECRETS,
  LIST_APP_SECRET_BUNDLE_ATTACHMENTS,
  LIST_SECRET_CHANGE_PROPOSALS,
} from "@/graphql/services/services.queries";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";
import { handleVersionMismatch } from "@/lib/apollo/version-mismatch";

import {
  APP_SECRET_BUNDLES_LIST,
  APP_SECRETS_LIST,
  type SecretsSection,
  selectBundles,
  selectSecrets,
} from "./secrets-list";
import type { AppSecret, AppSecretBundleAttachment, RevealedSecretData } from "./secrets.types";

export const ALL_ENVS = "__all__";

/** #679 — short label for the scope badge on each secret row. */
export function scopeBadgeLabel(scope: string): string {
  if (scope === "production") return "prod";
  if (scope === "preview") return "preview";
  if (scope.startsWith("preview:")) {
    const branch = scope.slice("preview:".length);
    return branch ? `preview:${branch}` : "preview";
  }
  return scope;
}

interface SecretsResp {
  astroliftAppSecrets: AppSecret[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface AttachmentsResp {
  astroliftAppSecretBundleAttachments: AppSecretBundleAttachment[];
}

/**
 * The app Secrets tab: queries, mutations, toasts, and the per-row reveal /
 * edit / rotate state those mutations drive. The data half of SecretsScreen.
 * `section` is the one on screen: only its list is fetched (Leo's page rule
 * 2), the keys on `keys`, the attached bundles on `bundles`.
 */
export function useAppSecrets(slug: string, section: SecretsSection = "keys") {
  const t = useTranslations("apps.secrets");
  const [envName, setEnvName] = React.useState<string>(ALL_ENVS);
  // revealedValues maps secret.id -> plaintext while revealed.
  const [revealedValues, setRevealedValues] = React.useState<Record<string, string>>({});
  // editingId: which row is in inline-edit mode (must be revealed first).
  const [editingId, setEditingId] = React.useState<string | null>(null);
  // #714 — rotatingId: which row is in inline-rotate mode. Same UI as
  // edit (reuses InlineValueEditor) but the save handler routes to
  // rotateAppSecret so the audit log carries action='app.secret.rotate'.
  const [rotatingId, setRotatingId] = React.useState<string | null>(null);
  // revealingId: which row's reveal mutation is currently in flight.
  // Tracked locally because Apollo's useMutation result doesn't expose
  // the in-flight input variables in a typed way across SDK versions.
  const [revealingId, setRevealingId] = React.useState<string | null>(null);

  const variables = {
    appSlug: slug,
    environmentName: envName === ALL_ENVS ? null : envName,
  };

  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
    fetchPolicy: "cache-and-network",
  });
  // #497 — fetch the app's current ``version`` so every
  // ``setAppSecret`` mutation can carry ``ifMatchVersion``. Cheap query
  // (id + version only); Apollo will merge into any other cache entry
  // for this app slug.
  const appVersion = useQuery<{ astroliftApp: { id: string; version: number } | null }>(
    GET_APP_VERSION,
    { variables: { slug }, fetchPolicy: "cache-and-network" }
  );
  const secrets = useQuery<SecretsResp>(LIST_APP_SECRETS, {
    variables,
    fetchPolicy: "cache-and-network",
    skip: section !== "keys",
  });
  const attachments = useQuery<AttachmentsResp>(LIST_APP_SECRET_BUNDLE_ATTACHMENTS, {
    variables,
    fetchPolicy: "cache-and-network",
    skip: section !== "bundles",
  });
  // #488 — show inline "N pending proposal" banner when the app has
  // pending secret-change proposals. Poll lazily; the banner is
  // ambient context, not a primary action surface.
  const pendingProposals = useQuery<{
    astroliftSecretChangeProposals: AstroliftSecretChangeProposal[];
  }>(LIST_SECRET_CHANGE_PROPOSALS, {
    variables: { appSlug: slug, status: "pending" },
    fetchPolicy: "cache-and-network",
    pollInterval: 60_000,
  });

  const refetch = [
    { query: LIST_APP_SECRETS, variables },
    {
      query: LIST_APP_SECRET_BUNDLE_ATTACHMENTS,
      variables,
    },
  ];

  const [setSecret, setState] = useMutation<{
    setAppSecret: MutationResult<{
      appSlug: string;
      key: string;
      rawManifestStaged: string;
    }>;
  }>(SET_APP_SECRET, { refetchQueries: refetch, awaitRefetchQueries: true });
  // #714 — same shape as set; mutation differs only in the audit
  // action it emits (`app.secret.rotate` vs `app.secret.set`). Sharing
  // a single InlineValueEditor + handleInlineSave; the parent picks
  // the right mutation based on rotatingId.
  const [rotateSecret, rotateState] = useMutation<{
    rotateAppSecret: MutationResult<{
      appSlug: string;
      key: string;
      rawManifestStaged: string;
    }>;
  }>(ROTATE_APP_SECRET, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [deleteSecret, deleteState] = useMutation<{
    deleteAppSecret: MutationResult<{
      appSlug: string;
      key: string;
      rawManifestStaged: string;
    }>;
  }>(DELETE_APP_SECRET, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [bulkImport, bulkState] = useMutation<{
    bulkImportAppSecrets: MutationResult<{
      appSlug: string;
      keysSet: string[];
      rawManifestStaged: string;
    }>;
  }>(BULK_IMPORT_APP_SECRETS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [detachBundle, detachState] = useMutation<{
    detachSecretBundle: MutationResult<{ attachmentId: string }>;
  }>(DETACH_SECRET_BUNDLE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [revealSecret] = useMutation<{
    revealAppSecret: MutationResult<RevealedSecretData>;
  }>(REVEAL_APP_SECRET);

  const busy = setState.loading || deleteState.loading || bulkState.loading || detachState.loading;
  const list = secrets.data?.astroliftAppSecrets ?? [];
  const envList = envs.data?.astroliftEnvironments ?? [];
  const attachmentList = attachments.data?.astroliftAppSecretBundleAttachments ?? [];
  // The keys are the tab's default section, so their list state is the URL's;
  // the bundles sit under `?section=bundles`, which a URL list state would
  // drop on its first filter change, so theirs is in memory.
  const keysList = useListState(APP_SECRETS_LIST);
  const bundlesList = useLocalListState(APP_SECRET_BUNDLES_LIST);
  const keys = selectSecrets(list, keysList.filters, keysList.state);
  const bundles = selectBundles(attachmentList, bundlesList.filters, bundlesList.state);

  /** Resolves true when the delete confirm may open for this row. */
  function onRequestDelete(s: AppSecret): boolean {
    if (s.source !== "literal") {
      toast.error(
        s.source === "bundle"
          ? "Detach the bundle to remove this key."
          : "Managed-service envelope keys aren't editable directly."
      );
      return false;
    }
    return true;
  }

  /** Throws on failure, so the confirm dialog stays open with the error. */
  async function onDelete(s: AppSecret) {
    const { data } = await deleteSecret({
      variables: { input: { appSlug: slug, key: s.key } },
    });
    if (data?.deleteAppSecret.ok) {
      toast.success(`Deleted ${s.key}`);
      setRevealedValues((prev) => {
        if (!(s.id in prev)) return prev;
        const next = { ...prev };
        delete next[s.id];
        return next;
      });
    } else {
      throw new Error(data?.deleteAppSecret.errors?.[0]?.message ?? "Delete failed");
    }
  }

  async function onToggleReveal(s: AppSecret) {
    if (s.source !== "literal") {
      toast.error(t("reveal.literalOnly"));
      return;
    }
    if (s.id in revealedValues) {
      setRevealedValues((prev) => {
        const next = { ...prev };
        delete next[s.id];
        return next;
      });
      if (editingId === s.id) setEditingId(null);
      if (rotatingId === s.id) setRotatingId(null);
      return;
    }
    setRevealingId(s.id);
    try {
      const { data } = await revealSecret({
        variables: { input: { appSlug: slug, secretId: s.id } },
      });
      if (data?.revealAppSecret.ok && data.revealAppSecret.data) {
        const value = data.revealAppSecret.data.value;
        setRevealedValues((prev) => ({ ...prev, [s.id]: value }));
        toast.success(t("reveal.toastRevealed"));
      } else {
        const err = data?.revealAppSecret.errors?.[0];
        if (err?.code === "PERMISSION_DENIED") {
          toast.error(t("reveal.permissionDenied"));
        } else {
          toast.error(err?.message ?? t("reveal.failed"));
        }
      }
    } catch (err) {
      toast.error((err as Error).message ?? t("reveal.failed"));
    } finally {
      setRevealingId(null);
    }
  }

  function onStartEdit(s: AppSecret) {
    setRotatingId(null);
    setEditingId(s.id);
  }

  function onStartRotate(s: AppSecret) {
    setEditingId(null);
    setRotatingId(s.id);
  }

  function onCancelEdit() {
    setEditingId(null);
    setRotatingId(null);
  }

  async function onInlineSave(s: AppSecret, nextValue: string): Promise<boolean> {
    // #714 — rotate vs set picks the mutation; both share the same
    // input shape + optimistic-concurrency check.
    // #679 — inline edits preserve the row's existing scope; the
    // dialog is the surface for changing scope.
    const isRotate = rotatingId === s.id;
    const input = {
      appSlug: slug,
      key: s.key,
      value: nextValue,
      scope: s.scope || "all",
      ifMatchVersion: appVersion.data?.astroliftApp?.version ?? null,
    };
    if (isRotate) {
      const { data } = await rotateSecret({ variables: { input } });
      if (data?.rotateAppSecret.ok) {
        toast.success(t("edit.toastSaved", { key: s.key }));
        setRevealedValues((prev) => ({ ...prev, [s.id]: nextValue }));
        setRotatingId(null);
        return true;
      }
      if (
        handleVersionMismatch(data?.rotateAppSecret, {
          label: "app",
          onRefresh: () => appVersion.refetch(),
        })
      ) {
        return false;
      }
      toast.error(data?.rotateAppSecret.errors?.[0]?.message ?? "Rotate failed");
      return false;
    }
    const { data } = await setSecret({ variables: { input } });
    if (data?.setAppSecret.ok) {
      toast.success(t("edit.toastSaved", { key: s.key }));
      setRevealedValues((prev) => ({ ...prev, [s.id]: nextValue }));
      setEditingId(null);
      return true;
    }
    if (
      handleVersionMismatch(data?.setAppSecret, {
        label: "app",
        onRefresh: () => appVersion.refetch(),
      })
    ) {
      return false;
    }
    toast.error(data?.setAppSecret.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  /** Throws on failure, so the confirm dialog stays open with the error. */
  async function onDetach(a: AppSecretBundleAttachment) {
    const { data } = await detachBundle({
      variables: { input: { attachmentId: a.id } },
    });
    if (data?.detachSecretBundle.ok) {
      toast.success(t("attached.toastDetached", { name: a.bundleName }));
    } else {
      throw new Error(data?.detachSecretBundle.errors?.[0]?.message ?? "Detach failed");
    }
  }

  /** Resolves true when saved (the view closes the sheet). */
  async function onSetSecret(key: string, value: string, scope: string): Promise<boolean> {
    const { data } = await setSecret({
      variables: {
        input: {
          appSlug: slug,
          key,
          value,
          scope,
          ifMatchVersion: appVersion.data?.astroliftApp?.version ?? null,
        },
      },
    });
    if (data?.setAppSecret.ok) {
      toast.success(`Set ${key}${scope !== "all" ? ` (${scopeBadgeLabel(scope)})` : ""}`);
      return true;
    }
    if (
      handleVersionMismatch(data?.setAppSecret, {
        label: "app",
        onRefresh: () => appVersion.refetch(),
      })
    ) {
      return false;
    }
    toast.error(data?.setAppSecret.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  /** Resolves true when imported (the view closes the sheet). */
  async function onBulkImport(dotenvText: string): Promise<boolean> {
    const { data } = await bulkImport({
      variables: { input: { appSlug: slug, dotenvText } },
    });
    if (data?.bulkImportAppSecrets.ok) {
      const keys = data.bulkImportAppSecrets.data?.keysSet ?? [];
      toast.success(`Imported ${keys.length} key${keys.length === 1 ? "" : "s"}`);
      return true;
    }
    toast.error(data?.bulkImportAppSecrets.errors?.[0]?.message ?? "Import failed");
    return false;
  }

  return {
    slug,
    envName,
    setEnvName,
    environments: envList,
    section,
    secrets: list,
    keysList,
    keyRows: keys.rows,
    keyTotal: keys.totalCount,
    secretsLoading: secrets.loading && list.length === 0,
    /** The secrets query failed with nothing cached. */
    secretsError: secrets.data ? null : (secrets.error ?? null),
    retrySecrets: (): void => {
      void secrets.refetch();
    },
    attachments: attachmentList,
    bundlesList,
    bundleRows: bundles.rows,
    bundleTotal: bundles.totalCount,
    attachmentsLoading: attachments.loading && attachmentList.length === 0,
    attachmentsError: attachments.data ? null : (attachments.error ?? null),
    retryAttachments: (): void => {
      void attachments.refetch();
    },
    pendingProposals: pendingProposals.data?.astroliftSecretChangeProposals ?? [],
    revealedValues,
    editingId,
    rotatingId,
    revealingId,
    busy,
    rotating: rotateState.loading,
    detaching: detachState.loading,
    onToggleReveal,
    onStartEdit,
    onStartRotate,
    onCancelEdit,
    onInlineSave,
    onRequestDelete,
    onDelete,
    onDetach,
    onSetSecret,
    onBulkImport,
  };
}
