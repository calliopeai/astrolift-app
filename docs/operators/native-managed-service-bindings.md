# Native managed-service binding namespaces

Generated Service endpoints belong to the namespace recorded in the managed
service's four-part handle, independently of the app environment consuming the
binding. Host aliases and URI envelopes use that same locator. A later operator
namespace default cannot redirect a saved service. Reconcile legacy two-part
handles before refreshing these bindings; no namespace is guessed from a name.

New namespace-qualified hosts use `<service>.<namespace>.svc`. The pod's DNS search
suffix supplies its actual cluster domain. CNPG's `DATABASE_URL` selects the
operator's rotating `fqdn-uri` Secret field, which includes the operator's
configured cluster domain; passwords and other credential fields remain Secret
references. Existing externally configured endpoints are retained literally.

References: [Kubernetes cross-namespace Service discovery](https://kubernetes.io/docs/concepts/services-networking/service/),
[CNPG application connections and Secret fields](https://cloudnative-pg.io/docs/devel/applications/).

## Registry audit (#2092)

The current `k8s_native.plugin.PLUGIN.managed_service_drivers` registers **24**
pairs, implemented by 22 modules. Every registered binding implementation was
inspected, including aliases, controller status endpoints and volume bindings.

| Registered kind / variant | Endpoint authority |
|---|---|
| postgres / cnpg | Handle namespace: RW host aliases; operator `fqdn-uri` URL. Preview slice lifecycle requires separate repair. |
| redis / operator | Handle namespace: `REDIS_HOST` and `REDIS_URL`. |
| mysql / operator | Handle namespace: `MYSQL_HOST`. |
| document_db / mongodb_operator | Handle namespace: MongoDB SRV URI. |
| event_stream / nats | Handle namespace: NATS broker URI. |
| queue / rabbitmq_operator | Handle namespace: RabbitMQ host. |
| event_stream / kafka_strimzi | Handle namespace: bootstrap host and port; removed legacy namespace guessing. |
| cache / memcached | Already handle-qualified headless host and server list. |
| faas / knative_service | Ready controller status URL; retained literally. |
| api_gateway / gateway_api | Programmed gateway address; retained literally. |
| event_bus / knative_eventing | Ready Broker status address and URI alias; retained literally. |
| workflow_engine / argo_workflows | Explicit operator Argo Server URL; no generated Service endpoint. |
| workflow_engine / temporal | Existing handle-qualified frontend address; operator external address retained. |
| model_endpoint / kserve | Ready inference status HTTP/gRPC URLs; retained literally. |
| model_endpoint / vllm | Existing handle-qualified internal OpenAI endpoint. |
| observability / kube_prometheus_stack | Existing monitoring-namespace-qualified endpoints; explicit endpoints retained. |
| object_store / s3_compatible_existing | Explicit verified external S3 endpoint; retained literally. |
| object_store / seaweedfs_operator | Existing operator-namespace-qualified generated endpoint; explicit endpoint retained. |
| mssql / sqlserver_express | Existing handle-qualified SQL Service host. |
| search / opensearch_operator | Existing handle-qualified search endpoint. |
| vector_index / opensearch_operator_vector | Same endpoint implementation as search. |
| filesystem / nfs_csi | Explicit NFS server and volume metadata; retained literally. |
| filesystem / storage_class_pvc | Consumer-local PVC template; no server endpoint. |
| filesystem / rook_cephfs | Consumer-local CSI PVC template; no server endpoint. |

## Verification boundary

Recording provision/binding tests assert the same persisted cluster and namespace,
including changed operator defaults, both CNPG host aliases and Redis URI aliases.
PostgreSQL tests cover binding synchronization and consumer-namespace Secret
materialization without credential rewriting. The optional disposable kind proof
uses lightweight HTTP servers behind seven generated Service names and a custom
`private.test` cluster DNS domain. This proves DNS and TCP/HTTP routing from another
namespace; it does not certify database operators, authentication or database
protocols. No customer cluster is contacted by these tests.
