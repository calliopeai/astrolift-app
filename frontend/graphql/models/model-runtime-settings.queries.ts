import { gql } from "@apollo/client";
export const MODEL_RUNTIME_SETTINGS_FIELDS = gql`
  fragment ModelRuntimeSettingsFields on ClusterModelRuntimeSettings {
    organizationId
    clusterId
    providerId
    clusterVersion
    providerVersion
    observedAt
    modes {
      computeMode
      configured
      reason
      hardwareAdmission
      declaration {
        image
        version
        packageVersion
        architecture
        nodeSelector {
          key
          value
        }
        supportedDtypes
        defaultDtype
        defaultMaxModelLen
        maxModelLenCeiling
        defaultMaxNumSeqs
        maxNumSeqsCeiling
        cpuRequestCeiling
        memoryRequestCeiling
        gpuCountCeiling
        hardwareCertified
        hardwareEvidence
      }
    }
  }
`;
export const GET_CLUSTER_MODEL_RUNTIME_SETTINGS = gql`
  query GetClusterModelRuntimeSettings(
    $organizationId: GUID!
    $clusterId: GUID!
    $expectedProviderId: GUID!
  ) {
    clusterModelRuntimeSettings(
      organizationId: $organizationId
      clusterId: $clusterId
      expectedProviderId: $expectedProviderId
    ) {
      ...ModelRuntimeSettingsFields
    }
  }
  ${MODEL_RUNTIME_SETTINGS_FIELDS}
`;
