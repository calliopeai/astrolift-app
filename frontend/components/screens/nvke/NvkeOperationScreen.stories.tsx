import { INSTALL_IMAGE_BINDING_PROPS } from "./NvkeOperatorInstallPlanImageBindingPanel.stories";
import { WorkloadBound as WORKLOAD_IMAGE_STORY } from "./NvkeOperatorInstallPlanImagePanel.stories";
import { INSTALL_WORKLOAD_PROPS } from "./NvkeOperatorInstallPlanWorkloadPanel.stories";
import { INSTALL_LOCAL_IMAGE_PROPS } from "./NvkeOperatorInstallPlanLocalImagePanel.stories";
import { INSTALL_IMAGE_PROPS } from "./NvkeOperatorInstallPlanImagePanel.stories";
import { INSTALL_VERIFICATION_PROPS } from "./NvkeOperatorInstallPlanVerificationPanel.stories";
import { INSTALL_CONTEXT_PROPS } from "./NvkeOperatorInstallPlanContextPanel.stories";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";
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

export const InstallPlanTree: Story = {
  args: { ...InstallPlanCharts.args, operatorInstallPlanTree: INSTALL_TREE_PROPS },
};
export const InstallPlanTreeReadOnly: Story = {
  args: { ...InstallPlanTree.args, canAct: false },
};
export const InstallPlanTreePending: Story = {
  args: { ...InstallPlanTree.args, pending: true },
};

export const InstallPlanContext: Story = {
  args: { ...InstallPlanTree.args, operatorInstallPlanContext: INSTALL_CONTEXT_PROPS },
};
export const InstallPlanContextReadOnly: Story = {
  args: { ...InstallPlanContext.args, canAct: false },
};
export const InstallPlanContextPending: Story = {
  args: { ...InstallPlanContext.args, pending: true },
};

export const InstallPlanVerification: Story = {
  args: { ...InstallPlanContext.args, operatorInstallPlanVerification: INSTALL_VERIFICATION_PROPS },
};
export const InstallPlanVerificationReadOnly: Story = {
  args: { ...InstallPlanVerification.args, canAct: false },
};
export const InstallPlanVerificationPending: Story = {
  args: { ...InstallPlanVerification.args, pending: true },
};

export const InstallPlanImage: Story = {
  args: { ...InstallPlanVerification.args, operatorInstallPlanImage: INSTALL_IMAGE_PROPS },
};
export const InstallPlanImageReadOnly: Story = {
  args: { ...InstallPlanImage.args, canAct: false },
};
export const InstallPlanImagePending: Story = { args: { ...InstallPlanImage.args, pending: true } };

export const InstallPlanLocalImage: Story = {
  args: {
    ...InstallPlanVerification.args,
    operatorInstallPlanLocalImage: INSTALL_LOCAL_IMAGE_PROPS,
  },
};
export const InstallPlanLocalImageReadOnly: Story = {
  args: { ...InstallPlanLocalImage.args, canAct: false },
};
export const InstallPlanLocalImagePending: Story = {
  args: { ...InstallPlanLocalImage.args, pending: true },
};

export const InstallPlanWorkload: Story = {
  args: { ...InstallPlanVerification.args, operatorInstallPlanWorkload: INSTALL_WORKLOAD_PROPS },
};
export const InstallPlanWorkloadReadOnly: Story = {
  args: { ...InstallPlanWorkload.args, canAct: false },
};
export const InstallPlanWorkloadPending: Story = {
  args: { ...InstallPlanWorkload.args, pending: true },
};

export const InstallPlanWorkloadImage: Story = {
  args: {
    ...InstallPlanWorkload.args,
    operatorInstallPlanWorkloadImage: WORKLOAD_IMAGE_STORY.args as typeof INSTALL_IMAGE_PROPS,
  },
};

export const InstallPlanImageBinding: Story = {
  args: {
    ...InstallPlanWorkloadImage.args,
    operatorInstallPlanImageBinding: INSTALL_IMAGE_BINDING_PROPS,
  },
};
export const InstallPlanImageBindingReadOnly: Story = {
  args: { ...InstallPlanImageBinding.args, canAct: false },
};
export const InstallPlanImageBindingPending: Story = {
  args: { ...InstallPlanImageBinding.args, pending: true },
};
