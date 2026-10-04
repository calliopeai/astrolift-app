import { gql } from "@apollo/client";
export const GET_MODEL_SUBSCRIPTION_METRICS = gql`
  query GetModelSubscriptionMetrics(
    $organizationId: GUID!
    $serviceId: GUID!
    $subscriptionId: GUID!
    $expectedClusterId: GUID!
    $expectedProviderId: GUID!
    $start: DateTime!
    $end: DateTime!
  ) {
    astroliftModelSubscriptionMetrics(
      organizationId: $organizationId
      serviceId: $serviceId
      subscriptionId: $subscriptionId
      expectedClusterId: $expectedClusterId
      expectedProviderId: $expectedProviderId
      start: $start
      end: $end
    ) {
      serviceId
      clusterId
      subscriptionId
      start
      end
      retrievedAt
      stepSeconds
      scope
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
