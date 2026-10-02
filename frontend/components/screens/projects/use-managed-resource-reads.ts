"use client";

import { useLazyQuery, useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import type { CursorPage } from "@/components/data-table/types";
import type {
  GetProjectResourceQuery,
  ListProjectResourcePageQuery,
  ListProjectResourceAttachmentsPageQuery,
} from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  GET_PROJECT_RESOURCE,
  LIST_PROJECT_RESOURCE_PAGE,
  LIST_PROJECT_RESOURCE_ATTACHMENTS_PAGE,
} from "@/graphql/services/resource-reads.queries";
import {
  ATTACH_PROJECT_MANAGED_SERVICE,
  DETACH_PROJECT_MANAGED_SERVICE,
  REPROVISION_PROJECT_MANAGED_SERVICE,
  DEPROVISION_PROJECT_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import { PREVIEW_MANAGED_SERVICE_COST } from "@/graphql/services/services.queries";
import { useConfirm } from "@/hooks/use-confirm";
import type {
  ManagedResourceAttachment,
  ManagedResourceDetailProps,
} from "./ManagedResourceDetail";
import type { ProjectManagedResource } from "./ProjectManagedResourceList";
import type { ManagedServiceCostPreview } from "./use-project-resources";

/** Basic triage uses only scoped metadata. Price and writes remain explicit independent gates. */
export function useManagedResourceReads(
  projectId: string,
  canUpdate: boolean,
  canPreview: boolean
) {
  const copy = useTranslations("projectResources.reads");
  const confirm = useConfirm();
  const { org } = useActiveOrg();
  const { user } = useMe();
  const orgId = org?.id;
  const identityKey = orgId && user && projectId ? `${orgId}:${user.id}:${projectId}` : "";
  const [identityState, setIdentityState] = React.useState({ key: identityKey, epoch: 0 });
  const identity = React.useRef(identityState);
  const [resourceEpoch, setResourceEpoch] = React.useState(0);
  const [attachmentEpoch, setAttachmentEpoch] = React.useState(0);
  const [selection, setSelection] = React.useState<{
    key: string;
    row: ProjectManagedResource;
    serial: number;
  } | null>(null);
  const [metadata, setMetadata] = React.useState<{
    key: string;
    data: ProjectManagedResource | null;
    refused: boolean;
  } | null>(null);
  const [costs, setCosts] = React.useState<Record<string, ManagedServiceCostPreview>>({});
  const [costFailedKey, setCostFailedKey] = React.useState("");
  const [costBusyKey, setCostBusyKey] = React.useState("");
  const generation = React.useRef(0);
  const selectionGeneration = React.useRef(0);
  const costGeneration = React.useRef(0);
  if (identityState.key !== identityKey) {
    // Reset before children render under another identity, including A/B/A.
    setIdentityState({ key: identityKey, epoch: identityState.epoch + 1 });
    setSelection(null);
    setMetadata(null);
    setCosts({});
    setCostBusyKey("");
    setCostFailedKey("");
  }
  const scopeKey =
    identityKey && identityState.key === identityKey ? `${identityKey}:${identityState.epoch}` : "";
  React.useLayoutEffect(() => {
    identity.current = identityState;
    generation.current += 1;
  }, [identityState]);
  const target =
    selection?.key === scopeKey &&
    selection.row.projectId === projectId &&
    selection.row.organizationId === orgId
      ? selection.row
      : null;
  const targetKey = target
    ? `${scopeKey}:${target.id}:${target.contextRevision}:${selection?.serial}`
    : "";
  const latestTarget = React.useRef(targetKey);
  React.useLayoutEffect(() => {
    latestTarget.current = targetKey;
  }, [targetKey]);
  const isCurrent = () =>
    Boolean(
      targetKey &&
      latestTarget.current === targetKey &&
      identity.current.key === identityKey &&
      scopeKey === `${identityKey}:${identity.current.epoch}`
    );
  const resources = useScopedCursorTable<ProjectManagedResource>(
    {
      query: LIST_PROJECT_RESOURCE_PAGE,
      variables: { projectId },
      skip: !scopeKey,
      resetKey: `${scopeKey}:${resourceEpoch}`,
      searchVariable: "search",
      urlKey: "resource",
      extract: (data) =>
        (data as ListProjectResourcePageQuery | undefined)?.astroliftProjectManagedServicesPage,
    },
    scopeKey
  );
  const restartResources = () => {
    setResourceEpoch((epoch) => epoch + 1);
    if (resources.pageIndex === 0) resources.refetch();
  };
  const [loadMetadata] = useLazyQuery<GetProjectResourceQuery>(GET_PROJECT_RESOURCE, {
    fetchPolicy: "no-cache",
  });
  const loadDetail = React.useCallback(async () => {
    if (!target || latestTarget.current !== targetKey) return;
    const serial = ++generation.current;
    try {
      const response = await loadMetadata({
        variables: { projectId, id: target.id, expectedContextRevision: target.contextRevision },
        context: { queryDeduplication: false },
      });
      if (latestTarget.current !== targetKey || generation.current !== serial) return;
      const row = response.data?.astroliftProjectManagedService;
      setMetadata({
        key: targetKey,
        data:
          row?.id === target.id &&
          row.projectId === projectId &&
          row.organizationId === orgId &&
          row.contextRevision === target.contextRevision
            ? row
            : null,
        refused: Boolean(response.error || !row),
      });
    } catch {
      if (latestTarget.current === targetKey && generation.current === serial)
        setMetadata({ key: targetKey, data: null, refused: true });
    }
  }, [loadMetadata, projectId, targetKey, target, orgId]);
  React.useEffect(() => {
    void Promise.resolve().then(loadDetail);
    return () => {
      generation.current += 1;
    };
  }, [loadDetail]);
  const observed = metadata?.key === targetKey ? metadata : null;
  const ready = Boolean(target && observed?.data && !observed.refused);
  const current = ready ? (observed?.data ?? null) : null;
  const attachments = useScopedCursorTable<ManagedResourceAttachment>(
    {
      query: LIST_PROJECT_RESOURCE_ATTACHMENTS_PAGE,
      variables: {
        projectId,
        managedServiceId: target?.id ?? "",
        expectedContextRevision: target?.contextRevision,
      },
      resetKey: `${targetKey}:${attachmentEpoch}`,
      skip: !ready,
      extract: (data) => {
        const page = (data as ListProjectResourceAttachmentsPageQuery | undefined)
          ?.astroliftProjectManagedServiceAttachmentsPage;
        if (
          !page ||
          page.items.some(
            (row) => row.managedServiceId !== target?.id || row.clusterId !== target.clusterId
          )
        )
          return null;
        return page as CursorPage<ManagedResourceAttachment>;
      },
    },
    targetKey
  );
  const restartAttachments = () => {
    setAttachmentEpoch((epoch) => epoch + 1);
    if (attachments.pageIndex === 0) attachments.refetch();
  };
  const [loadCost] = useLazyQuery<{
    astroliftManagedServiceCostPreview: ManagedServiceCostPreview | null;
  }>(PREVIEW_MANAGED_SERVICE_COST, { fetchPolicy: "no-cache" });
  const costKey = current ? targetKey : "";
  const [attach, attachState] = useMutation<{
    attachProjectManagedService: MutationResult<unknown>;
  }>(ATTACH_PROJECT_MANAGED_SERVICE);
  const [detach, detachState] = useMutation<{
    detachProjectManagedService: MutationResult<unknown>;
  }>(DETACH_PROJECT_MANAGED_SERVICE);
  const [reprovision, reprovisionState] = useMutation<{
    reprovisionProjectManagedService: MutationResult<unknown>;
  }>(REPROVISION_PROJECT_MANAGED_SERVICE);
  const [deprovision, deprovisionState] = useMutation<{
    deprovisionProjectManagedService: MutationResult<unknown>;
  }>(DEPROVISION_PROJECT_MANAGED_SERVICE);
  const mutationLoading =
    attachState.loading ||
    detachState.loading ||
    reprovisionState.loading ||
    deprovisionState.loading;
  const canAct = canUpdate && ready && !mutationLoading && !attachments.isStale;

  async function onRefresh() {
    if (!target || !isCurrent()) return;
    const serial = ++generation.current;
    try {
      const response = await loadMetadata({
        variables: { projectId, id: target.id },
        context: { queryDeduplication: false },
      });
      if (!isCurrent() || generation.current !== serial) return;
      const fresh = response.data?.astroliftProjectManagedService;
      if (
        response.error ||
        !fresh ||
        fresh.id !== target.id ||
        fresh.projectId !== projectId ||
        fresh.organizationId !== orgId
      )
        throw new Error("unavailable");
      setSelection({ key: scopeKey, row: fresh, serial: ++selectionGeneration.current });
      setMetadata(null);
      setAttachmentEpoch((epoch) => epoch + 1);
      if (fresh.contextRevision === target.contextRevision) {
        // Unchanged proof still needs a new observed metadata read.
        void loadDetail();
        restartAttachments();
      }
    } catch {
      if (!isCurrent() || generation.current !== serial) return;
      setMetadata({ key: targetKey, data: null, refused: true });
      toast.error(copy("actionRefused"));
    }
  }
  async function onCost() {
    if (!current || !canPreview || !isCurrent()) return;
    const key = costKey;
    const serial = ++costGeneration.current;
    setCostBusyKey(key);
    try {
      const response = await loadCost({
        context: { queryDeduplication: false },
        variables: {
          managedServiceId: current.id,
          expectedContextRevision: current.contextRevision,
        },
      });
      if (!isCurrent() || serial !== costGeneration.current) return;
      const preview = response.data?.astroliftManagedServiceCostPreview;
      if (response.error || !preview || preview.managedServiceId !== current.id)
        throw new Error("unavailable");
      setCosts({ [key]: preview });
      setCostFailedKey("");
      setCostBusyKey("");
    } catch {
      if (isCurrent() && serial === costGeneration.current) {
        setCostFailedKey(key);
        setCostBusyKey("");
      }
    }
  }
  function changed() {
    setSelection(null);
    restartResources();
  }
  async function onReprovision() {
    if (!current || !canAct || !isCurrent()) return;
    try {
      const response = await reprovision({
        variables: {
          input: { managedServiceId: current.id, expectedContextRevision: current.contextRevision },
        },
      });
      if (!isCurrent()) return;
      if (!response.data?.reprovisionProjectManagedService.ok) throw new Error("refused");
      toast.success(copy("operationAccepted"));
      changed();
    } catch {
      if (isCurrent()) toast.error(copy("actionRefused"));
    }
  }
  async function onDeprovision() {
    if (!current || !canAct || !isCurrent()) return;
    const approved = await confirm({
      title: copy("deprovisionTitle", { name: current.name }),
      description: copy("deprovisionDescription"),
      confirmLabel: copy("deprovision"),
    });
    if (!approved || !isCurrent()) return;
    try {
      const response = await deprovision({
        variables: {
          input: {
            id: current.id,
            expectedContextRevision: current.contextRevision,
            deleteData: false,
            forceDestroy: false,
          },
        },
      });
      if (!isCurrent()) return;
      if (!response.data?.deprovisionProjectManagedService.ok) throw new Error("refused");
      toast.success(copy("operationAccepted"));
      changed();
    } catch {
      if (isCurrent()) toast.error(copy("actionRefused"));
    }
  }
  async function onDetach(id: string) {
    if (
      !current ||
      !canAct ||
      !isCurrent() ||
      !attachments.rows.some((row) => row.id === id && row.managedServiceId === current.id)
    )
      return;
    try {
      const response = await detach({
        variables: {
          input: {
            attachmentId: id,
            managedServiceId: current.id,
            expectedContextRevision: current.contextRevision,
          },
        },
      });
      if (!isCurrent()) return;
      if (!response.data?.detachProjectManagedService.ok) throw new Error("refused");
      restartAttachments();
    } catch {
      if (isCurrent()) toast.error(copy("actionRefused"));
    }
  }
  async function onAttach(consumer: {
    appEnvironmentId: string | null;
    agentEnvironmentSpecSlug: string | null;
  }) {
    if (!current || !canAct || !isCurrent()) return;
    try {
      const response = await attach({
        variables: {
          input: {
            managedServiceId: current.id,
            expectedContextRevision: current.contextRevision,
            ...consumer,
          },
        },
      });
      if (!isCurrent()) return;
      if (!response.data?.attachProjectManagedService.ok) throw new Error("refused");
      restartAttachments();
    } catch {
      if (isCurrent()) toast.error(copy("actionRefused"));
    }
  }
  const resourceTable = { ...resources, retry: restartResources, refetch: restartResources };
  const detailProps: ManagedResourceDetailProps = {
    target,
    current,
    loading: Boolean(target && !observed),
    refused: Boolean(target && observed && !ready),
    onClose: () => setSelection(null),
    onRefresh,
    attachments: { ...attachments, retry: restartAttachments, refetch: restartAttachments },
    canUpdate,
    canPreview,
    mutationLoading,
    onReprovision,
    onDeprovision,
    onDetach,
    onAttach,
    cost: costs[costKey],
    costLoading: Boolean(costKey && costBusyKey === costKey),
    costError: costFailedKey === costKey && Boolean(costKey),
    onCost,
  };
  return {
    resourceTable,
    resourceDetail: detailProps,
    onOpenResource: (row: ProjectManagedResource) => {
      if (
        !scopeKey ||
        identity.current.key !== identityKey ||
        scopeKey !== `${identityKey}:${identity.current.epoch}` ||
        resources.isStale ||
        resources.state !== "ready" ||
        row.projectId !== projectId ||
        row.organizationId !== orgId ||
        !resources.rows.some(
          (candidate) =>
            candidate.id === row.id && candidate.contextRevision === row.contextRevision
        )
      )
        return;
      setSelection({ key: scopeKey, row, serial: ++selectionGeneration.current });
    },
    refreshResources: restartResources,
  };
}
