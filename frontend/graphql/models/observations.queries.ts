import { gql } from "@apollo/client";

export const GET_MODEL_DEPLOYMENT_METRICS = gql`
  query GetModelDeploymentMetrics(
    $serviceId: GUID!
    $expectedClusterId: GUID!
    $expectedProviderId: GUID!
    $start: DateTime!
    $end: DateTime!
  ) {
    astroliftModelDeploymentMetrics(
      serviceId: $serviceId
      expectedClusterId: $expectedClusterId
      expectedProviderId: $expectedProviderId
      start: $start
      end: $end
    ) {
      serviceId
      clusterId
      start
      end
      retrievedAt
      stepSeconds
      scope
      sampleLimit
      metrics {
        key
        unit
        source
        state
        observedAt
        value
        aggregationWindowSeconds
        samples {
          timestamp
          value
        }
      }
    }
  }
`;

export const GET_CLUSTER_MODEL_DENSITY = gql`
  query GetClusterModelDensity(
    $clusterId: GUID!
    $expectedProviderId: GUID!
    $start: DateTime!
    $end: DateTime!
  ) {
    astroliftClusterModelDensity(
      clusterId: $clusterId
      expectedProviderId: $expectedProviderId
      start: $start
      end: $end
    ) {
      clusterId
      start
      end
      retrievedAt
      modelCount
      returnedCount
      scope
      source
      inventoryLimit
      truncated
      capacity {
        state
        source
        observedAt
        gpuDevices {
          resource
          devices
        }
        cpuCores
        memoryBytes
        vramBytes
        freshnessSeconds
      }
      items {
        serviceId
        name
        status
        desired {
          source
          observedAt
          replicas
          cpuCoresPerReplica
          memoryBytesPerReplica
          gpuDevicesPerReplica
          gpuResource
          totalCpuCores
          totalMemoryBytes
          totalGpuDevices
        }
        applied {
          source
          observedAt
          replicas
          cpuCoresPerReplica
          memoryBytesPerReplica
          gpuDevicesPerReplica
          gpuResource
          totalCpuCores
          totalMemoryBytes
          totalGpuDevices
        }
        observations {
          key
          unit
          source
          state
          observedAt
          value
          aggregationWindowSeconds
          samples {
            timestamp
            value
          }
        }
      }
    }
  }
`;
