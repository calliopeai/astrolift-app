import { INSTALL_CHARTS_PROPS } from "./NvkeOperatorInstallPlanChartsPanel.stories";
import { INSTALL_PLAN_PROPS } from "./NvkeOperatorInstallPlanPanel.stories";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { WORKFLOW_PROPS, WORKFLOW_HISTORY } from "./NvkeWorkflowPanel.stories";
import { NvkeOperationScreen } from "./NvkeOperationScreen";
import accessStory from "./NvkeWorkloadAccessPanel.stories";
import { PROVIDER_PROPS } from "./NvkeProvidersPanel.stories";
import { CREDENTIAL_PROPS } from "./NvkeCredentialPanel.stories";
import { COLLECTION_PROPS } from "./NvkeCredentialRequestPanel.stories";
import { INSTALL_REVIEW_PROPS } from "./NvkeOperatorInstallReviewPanel.stories";
import { OPERATOR_JOB_PROPS } from "./NvkeOperatorJobPanel.stories";
import { fakeController } from "@/components/data-table/fixtures";
import {
  DIAGNOSTIC_OBSERVATION,
  DIAGNOSTIC_ROW,
  OPERATION,
  OPERATION_DETAIL,
  MAINTENANCE_OBSERVATION,
} from "./fixtures";

export default {
  title: "Screens/NVKE/Operation",
  component: NvkeOperationScreen,
  args: OPERATION_DETAIL,
} satisfies Meta<typeof NvkeOperationScreen>;
type Story = StoryObj<typeof NvkeOperationScreen>;
export const OperatorJob: Story = {
  args: { operation: OPERATOR_JOB_PROPS.operation, operatorJob: OPERATOR_JOB_PROPS },
};
export const OperatorJobReadOnly: Story = {
  args: {
    operation: OPERATOR_JOB_PROPS.operation,
    operatorJob: OPERATOR_JOB_PROPS,
    canAct: false,
    canResume: false,
  },
};
export const OperatorJobUncertain: Story = {
  args: {
    operation: { ...OPERATOR_JOB_PROPS.operation, status: "UNCERTAIN" },
    operatorJob: {
      ...OPERATOR_JOB_PROPS,
      operation: { ...OPERATOR_JOB_PROPS.operation, status: "UNCERTAIN" },
      canReconcile: true,
    },
    canResume: false,
  },
};
export const CollectCredentials: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" },
    credentialRequest: COLLECTION_PROPS,
  },
};
export const Credentials: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "metrics.credentials",
      status: "SUCCEEDED",
      version: 12,
    },
    credentials: CREDENTIAL_PROPS,
  },
};
export const Maintenance: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" },
    maintenance: {
      observation: MAINTENANCE_OBSERVATION,
      loading: false,
      error: null,
      onRefresh: () => {},
    },
  },
};
export const ImportedAccess: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" },
    workloadAccess: accessStory.args,
  },
};
export const AwaitingReady: Story = {};
export const Loading: Story = { args: { operation: null, loading: true } };
export const LoadError: Story = {
  args: { operation: null, error: "NVKE data could not be read." },
};
export const Uncertain: Story = {
  args: {
    operation: {
      ...OPERATION,
      status: "UNCERTAIN",
      errorCode: "MUTATION_UNCERTAIN",
      stage: "reconciliation_required",
    },
  },
};
export const Succeeded: Story = {
  args: { operation: { ...OPERATION, status: "SUCCEEDED", stage: "ready" } },
};
export const InventorySucceeded: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "listClusters",
      isInventory: true,
      status: "SUCCEEDED",
      stage: "read_complete",
    },
  },
};
export const Failed: Story = {
  args: {
    operation: {
      ...OPERATION,
      status: "FAILED",
      stage: "request_refused",
      errorCode: "SOURCE_CHANGED",
    },
  },
};
export const ReadOnly: Story = { args: { canAct: false, canResume: false } };
export const LongStrings: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "longNativeOperationNameWithProviderDetails".repeat(5),
      stage: "waiting_for_provider_specific_readiness_".repeat(6),
      errorCode: "SAFE_PROVIDER_ERROR_CODE_".repeat(4),
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperationScreen {...args} />
    </div>
  ),
};

export const Diagnostics: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getClusterSpireServer", status: "SUCCEEDED" },
    diagnostics: {
      observation: DIAGNOSTIC_OBSERVATION,
      table: fakeController({ rows: [DIAGNOSTIC_ROW] }),
      loading: false,
      error: null,
      onRefresh: () => {},
    },
  },
};

export const NativeWorkflow: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "temporal.RenewK8sCertsV2",
      status: "SUCCEEDED",
      stage: "native_workflow_completed",
    },
  },
};
export const NativeResetUnconfirmed: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "temporal.reset.RotateEtcdEncryptionKey",
      status: "UNCERTAIN",
      stage: "native_reset_review_required",
      errorCode: "RESET_UNCONFIRMED",
    },
  },
};

export const NativeMaintenanceControls: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" },
    workflows: WORKFLOW_PROPS,
    maintenance: {
      observation: {
        ...MAINTENANCE_OBSERVATION,
        operationId: OPERATION.id,
        operationVersion: OPERATION.version,
        sourceSiteVersion: WORKFLOW_PROPS.site.version,
        renewal: null,
        mutationLockOwner: "etcd_key_rotation",
        etcdKeyRotation: {
          workflowId: WORKFLOW_HISTORY.workflowId,
          state: "failed",
          phase: "rewrite",
          retainedMutationLock: true,
          startedAt: null,
          lastProgressAt: null,
          completedAt: null,
          failureReason: "operation_failed",
        },
      },
      loading: false,
      error: null,
      onRefresh: () => {},
    },
  },
};

export const Providers: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "infrastructure.providers",
      status: "SUCCEEDED",
      version: 12,
    },
    providers: PROVIDER_PROPS,
  },
};

export const InstallReview: Story = {
  args: {
    operation: INSTALL_REVIEW_PROPS.operation,
    operatorInstallReview: INSTALL_REVIEW_PROPS,
    canResume: false,
  },
};
export const InstallReviewReadOnly: Story = {
  args: {
    operation: INSTALL_REVIEW_PROPS.operation,
    operatorInstallReview: INSTALL_REVIEW_PROPS,
    canAct: false,
    canResume: false,
  },
};

export const InstallPlan: Story = {
  args: {
    operation: INSTALL_PLAN_PROPS.operation,
    operatorInstallPlan: INSTALL_PLAN_PROPS,
    canResume: false,
  },
};
export const InstallPlanReadOnly: Story = {
  args: {
    operation: INSTALL_PLAN_PROPS.operation,
    operatorInstallPlan: INSTALL_PLAN_PROPS,
    canAct: false,
    canResume: false,
  },
};

export const InstallPlanCharts: Story = {
  args: {
    operation: INSTALL_CHARTS_PROPS.operation,
    operatorInstallPlan: INSTALL_PLAN_PROPS,
    operatorInstallPlanCharts: INSTALL_CHARTS_PROPS,
    canResume: false,
  },
};
export const InstallPlanChartsReadOnly: Story = {
  args: {
    ...InstallPlanCharts.args,
    canAct: false,
  },
};
