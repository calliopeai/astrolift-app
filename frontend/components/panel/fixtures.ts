/** Fixtures for Panel stories and tests: the long strings of spec 44 §8. */

export const LONG_SHA = "f1f9f11a0c2e4b7d9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f";

export const LONG_ARN =
  "arn:aws:eks:us-east-1:718519534729:cluster/prod-east-shared-multi-tenant-platform-cluster-for-regulated-workloads/nodegroup/general-purpose-arm64-graviton3-on-demand-with-a-very-long-name/0000";

export const LONG_URL =
  "https://prometheus.internal.prod-east.example.com/api/v1/query_range?query=sum(rate(container_cpu_usage_seconds_total[5m]))";

export const QUERY_ERROR = "Network error: upstream driver call timed out after 30000ms";

export const DEPLOY_FAILURE =
  "Image pull denied: ghcr.io/example/checkout@sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08 requires authentication";

export const WORKLOADS = ["checkout-web", "billing-api", "report-runner"];
