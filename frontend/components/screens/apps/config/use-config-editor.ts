"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  APPLY_STAGED_MANIFEST,
  PUSH_MANIFEST_TO_REPO,
  SYNC_MANIFEST_FROM_REPO,
  UPDATE_MANIFEST,
} from "@/graphql/registry/registry.mutations";
import { GET_APP, GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { detectTomlSchema } from "@/lib/manifest/schema-detect";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface ManifestStagePayload {
  id: string;
  syncState: string;
  rawManifest: string;
  rawManifestStaged: string;
}

interface ManifestPushPayload {
  id: string;
  prUrl: string;
  branchName: string;
  note: string;
}

export interface RenderedManifest {
  appSlug: string;
  namespace: string;
  resources: Record<string, unknown>;
  error?: string | null;
  errorPath?: string | null;
  errorLine?: number | null;
  errorColumn?: number | null;
}

interface RenderedResp {
  astroliftRenderedManifest: RenderedManifest | null;
}

export interface ManifestConflict {
  theirs: string;
  serverUpdatedAt: string;
}

/**
 * The app manifest behind the config tab: the TOML draft, conflict detection
 * against edits made elsewhere, the rendered-resources preview, and the
 * save / sync / push / apply mutations. The data half of ConfigEditorScreen.
 */
export function useConfigEditor(slug: string) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp ?? null;

  // Editor draft. Staged content takes precedence over raw because
  // it represents the most recent unsaved-to-repo state.
  const initialDraft = a?.rawManifestStaged?.length ? a.rawManifestStaged : (a?.rawManifest ?? "");
  const [draft, setDraft] = React.useState<string>(initialDraft);
  const [draftLoaded, setDraftLoaded] = React.useState(false);

  // astrolift.toml carries two schemas: an app manifest or an agent config repo
  // (#1172). The Form tab renders the matching builder; the Code tab, save,
  // sync/push, and rendered panels are shared. Detection tracks the live draft
  // so a Code-view edit that changes the schema swaps to the right builder.
  const schemaFamily = React.useMemo(() => detectTomlSchema(draft), [draft]);

  // Snapshot of the server's "effective" manifest at the moment the
  // operator started editing. We compare against this on every refetch
  // to detect "someone else changed it under me" conflicts. The check
  // fires when (a) we have a draft locally that differs from the
  // server's current effective text AND (b) the server's effective text
  // also differs from the snapshot — meaning both sides moved.
  const baselineRef = React.useRef<string>("");
  // True while our own Save is in flight. awaitRefetchQueries delivers the
  // refetched app before updateManifest resolves, and the server echoes the
  // text back with [env] values masked for a viewer who can't reveal them
  // (#1920). Without this, the effect below reads that masked echo of our
  // own save as someone else's edit. saveSettled re-runs the check once
  // the save is done, against the baseline it left behind.
  const savingRef = React.useRef(false);
  const [saveSettled, setSaveSettled] = React.useState(0);
  const [conflict, setConflict] = React.useState<ManifestConflict | null>(null);

  React.useEffect(() => {
    if (a && !draftLoaded) {
      setDraft(initialDraft);
      baselineRef.current = initialDraft;
      setDraftLoaded(true);
    }
    // initialDraft derives from `a`; pin on `a.id`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a?.id]);

  // Conflict detection. Runs whenever the server payload changes —
  // refetchQueries from our own mutations are no-ops here because we
  // also reset baselineRef on save/sync. A change driven by *another*
  // editor (different tab / different user) shows up as: server text
  // moved away from our baseline, and our draft also moved away.
  React.useEffect(() => {
    if (!a || !draftLoaded || savingRef.current) return;
    const serverEffective = a.rawManifestStaged?.length
      ? a.rawManifestStaged
      : (a.rawManifest ?? "");
    const baseline = baselineRef.current;
    if (serverEffective === baseline) return; // server hasn't moved
    if (draft === baseline) {
      // We haven't edited yet — silently roll forward.
      setDraft(serverEffective);
      baselineRef.current = serverEffective;
      return;
    }
    if (draft === serverEffective) {
      // We somehow already match — adopt the server timestamp.
      baselineRef.current = serverEffective;
      return;
    }
    // Both moved: real conflict.
    setConflict({
      theirs: serverEffective,
      serverUpdatedAt: a.updatedAt ?? new Date().toISOString(),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a?.rawManifest, a?.rawManifestStaged, a?.updatedAt, saveSettled]);

  const rendered = useQuery<RenderedResp>(GET_RENDERED_MANIFEST, {
    variables: { appSlug: slug, environmentName: null, imageTag: null },
    skip: !a,
    fetchPolicy: "cache-and-network",
  });

  const refetch = [{ query: GET_APP, variables: { slug } }];
  const [updateManifest, updateState] = useMutation<{
    updateManifest: MutationResult<ManifestStagePayload>;
  }>(UPDATE_MANIFEST, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [syncManifest, syncState] = useMutation<{
    syncManifestFromRepo: MutationResult<ManifestStagePayload>;
  }>(SYNC_MANIFEST_FROM_REPO, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [pushManifest, pushState] = useMutation<{
    pushManifestToRepo: MutationResult<ManifestPushPayload>;
  }>(PUSH_MANIFEST_TO_REPO, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [applyManifest, applyState] = useMutation<{
    applyStagedManifest: MutationResult<ManifestStagePayload>;
  }>(APPLY_STAGED_MANIFEST, { refetchQueries: refetch, awaitRefetchQueries: true });

  const busy = updateState.loading || syncState.loading || pushState.loading || applyState.loading;

  // Key NAMES only (never values) that applying the staged buffer would
  // change, across [env] and every container/job/task env table --
  // computed by the server with the same diff applyStagedManifest gates
  // on, so the confirm dialog lists what the server treats as a secret
  // change (#1759 adversarial review).
  const changedEnvKeys = a?.stagedEnvChanges ?? [];

  async function handleSave() {
    if (!a) return;
    const submitted = draft;
    savingRef.current = true;
    try {
      const { data } = await updateManifest({
        variables: { input: { id: a.id, rawManifest: submitted } },
      });
      if (data?.updateManifest.ok) {
        // Adopt the server's copy of what we saved, which masks [env] values
        // this viewer can't reveal. Saving that masked text again keeps the
        // stored values, and anything typed during the save is left alone.
        const saved = data.updateManifest.data;
        const echoed = saved?.rawManifestStaged?.length
          ? saved.rawManifestStaged
          : (saved?.rawManifest ?? submitted);
        baselineRef.current = echoed;
        setDraft((current) => (current === submitted ? echoed : current));
        toast.success("Draft saved");
      } else {
        toast.error(data?.updateManifest.errors?.[0]?.message ?? "Save failed");
      }
    } finally {
      savingRef.current = false;
      setSaveSettled((n) => n + 1);
    }
  }

  async function handleSync() {
    if (!a) return;
    const { data } = await syncManifest({ variables: { input: { id: a.id } } });
    if (data?.syncManifestFromRepo.ok) {
      const next = data.syncManifestFromRepo.data;
      const text = next?.rawManifestStaged || next?.rawManifest || "";
      setDraft(text);
      baselineRef.current = text;
      toast.success("Pulled from repo");
    } else {
      throw new Error(
        data?.syncManifestFromRepo.errors?.[0]?.message ?? "Couldn't pull from the repo"
      );
    }
  }

  async function handleApply() {
    if (!a) return;
    const { data } = await applyManifest({
      variables: {
        input: { id: a.id, expectedStagedHash: a.rawManifestStagedHash },
      },
    });
    if (data?.applyStagedManifest.ok) {
      const next = data.applyStagedManifest.data;
      const text = next?.rawManifest || "";
      setDraft(text);
      baselineRef.current = text;
      toast.success("Applied");
    } else {
      throw new Error(data?.applyStagedManifest.errors?.[0]?.message ?? "Apply failed");
    }
  }

  async function handlePush() {
    if (!a) return;
    const { data } = await pushManifest({ variables: { input: { id: a.id } } });
    const result = data?.pushManifestToRepo;
    if (result?.ok) {
      const note = result.data?.note;
      if (note === "nothing_to_push") {
        toast.message("No staged changes to push.");
      } else if (result.data?.prUrl) {
        toast.success(`PR opened: ${result.data.branchName}`);
      } else {
        toast.success("Push complete");
      }
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Push failed");
    }
  }

  function dismissConflictKeepMine() {
    if (!conflict) return;
    // Treat our current draft as the new baseline so the next refetch
    // doesn't immediately re-fire. The next Save will write our draft.
    baselineRef.current = conflict.theirs;
    setConflict(null);
  }

  function adoptTheirs() {
    if (!conflict) return;
    setDraft(conflict.theirs);
    baselineRef.current = conflict.theirs;
    setConflict(null);
    toast.message("Loaded the server's copy.");
  }

  function closeConflict() {
    setConflict(null);
  }

  return {
    app: a,
    loading: app.loading && !a,
    draft,
    setDraft,
    schemaFamily,
    isDirty: a
      ? draft !== (a.rawManifestStaged?.length ? a.rawManifestStaged : a.rawManifest)
      : false,
    rendered: rendered.data?.astroliftRenderedManifest ?? null,
    renderedLoading: rendered.loading && !rendered.data,
    busy,
    saving: updateState.loading,
    applying: applyState.loading,
    changedEnvKeys,
    conflict,
    handleSave,
    handleSync,
    handleApply,
    handlePush,
    dismissConflictKeepMine,
    adoptTheirs,
    closeConflict,
  };
}
