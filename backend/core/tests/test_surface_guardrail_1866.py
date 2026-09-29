"""Every surface declares a permission and the scope it checks it at (#1866).

The test walks the running objects, not the source text:

* **GraphQL.** Every Query, Mutation and Subscription root field of both
  served schemas (``config.schema.schema`` and the login-only
  ``schema_auth``), named as clients see them. Each resolver's
  ``__wrapped__`` chain is walked and the real ``@tenant_scoped`` and
  ``@require_permission`` layers are recognised by their code objects, so a
  look-alike decorator or a renamed import cannot pass for them. A field
  passes when it has both and every permission gate declares its scope:
  ``any_scope=True`` (a collection that narrows its rows) or a scope
  factory. Each factory is then probed with its target absent, inside a
  tenant. One that answers ``None`` runs the targetless check, where the
  team or project the request selected stands in for the target (#1745),
  so it fails too.
* **URL conf and WebSockets.** Every route of the Django resolver and every
  entry of ``config.asgi.WEBSOCKET_ROUTES`` needs a ``route_auth``
  declaration (``core.permissions.RouteAuth``) on its view. A Django admin
  view counts as declared when it runs behind ``AdminSite.admin_view``
  (recognised by that wrapper's code object), which requires an active
  staff session, with ``ModelAdmin`` adding the model permissions.
* **MCP.** Every tool in ``MCP_TOOL_META`` needs a handler, an API-token
  scope, a permission and a ``TOOL_SCOPES`` entry whose factory never
  answers ``None``.

Anything that fails must be on :data:`ALLOWED` (public, self-service or
install-wide by design, with the reason) or :data:`GAPS` (tracked, one
issue per area, with what it checks today). An entry that no longer fails
or no longer exists fails the test, so both lists only shrink.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.django_db

# ---------------------------------------------------------------------------
# Exemptions by design. Each needs the reason a reviewer would ask for.
# ---------------------------------------------------------------------------

ALLOWED: dict[str, str] = {
    # -- Public by design: no caller identity, no tenant data -----------------
    "/": "public: the service banner (name, version, uptime), no tenant data",
    "/app/": "public: the backend landing page (version, environment, links), no tenant data",
    "/.well-known/apple-app-site-association": "public: the iOS universal-link file the OS fetches anonymously",
    "/.well-known/assetlinks.json": "public: the Android app-link file the OS fetches anonymously",
    "/^favicon\\.ico$": "public: redirect to the static favicon",
    "/health/": "public: the load balancer's health probe",
    "/health/<str:subset>/": "public: the load balancer's health probe, one check subset",
    "/app/admin/login/": "public: the Django admin sign-in page; every other admin view runs behind admin_view",
    "auth:Query.ok": "public: the login schema's liveness field",
    "Query.astroliftPlatformApiUrl": "install-wide constant (PLATFORM_API_URL) for the CI setup page; refuses anonymous callers",
    "Query.astroliftProviderPlugins": "install-wide provider plugin catalog, the same for every org; refuses anonymous callers",
    "Query.formFieldTypes": "the static palette of form widget kinds; no tenant data",
    "Query.agentRuntimes": "install-wide agent runtime catalog, the same for every org; refuses anonymous callers",
    "Query.astroliftServerInfo": "public: install and build metadata the shell renders, no tenant data",
    # -- Sign-in, sign-out and session flows ----------------------------------
    "auth:Mutation.login": "auth flow: username/password sign-in on the login-only schema",
    "auth:Mutation.logout": "auth flow: ends the caller's own session",
    "Mutation.login": "auth flow: username/password sign-in (legacy boilerworks mutation)",
    "Mutation.logout": "auth flow: ends the caller's own session (legacy boilerworks mutation)",
    "/app/auth1/login": "auth flow: starts the IdP sign-in",
    "/app/auth1/logout": "auth flow: ends the caller's own session",
    "/app/auth1/callback": "auth flow: the IdP's OIDC callback",
    "/app/auth1/session": "auth flow: exchanges an Auth0 token for a session",
    "/app/auth1/dev-login": "auth flow: DEBUG-only local sign-in, 404 otherwise",
    "/app/auth1/local-login": "auth flow: username/password sign-in when the install enables it",
    "/app/auth1/active-idp.json": "auth flow: the IdP catalog the sign-in page renders before any session exists",
    "/app/auth1/elevate-sso/": "auth flow: step-up re-authentication of the caller's own session",
    "/app/auth1/elevate-sso/callback/": "auth flow: step-up re-authentication callback for the caller's own session",
    "/api/cli/v1/auth/start": "auth flow: CLI device flow start",
    "/api/cli/v1/auth/complete": "auth flow: CLI device flow completion (the device code is the proof)",
    "/api/cli/v1/auth/refresh": "auth flow: CLI token refresh (the refresh token is the proof)",
    "/api/cli/v1/auth/signout": "auth flow: CLI sign-out of the presented token",
    "/app/cli/auth/device/<str:session_guid>/": "auth flow: the signed-in user approves their own device session",
    "Mutation.acceptInvitation": "auth flow: the invitation token is the proof, matched to the caller's email",
    "Mutation.approveDeploymentByToken": "magic link: the single-use approval token is the proof",
    "Mutation.rejectDeploymentByToken": "magic link: the single-use rejection token is the proof",
    # -- Self-service: the caller's own profile, sessions, devices, settings --
    "Query.me": "self-service: the caller's own profile",
    "Query.astroliftMyProfile": "self-service: the caller's own editable profile",
    "Query.astroliftOrganizations": "self-service: the orgs the caller is an active member of, to pick a tenant from",
    "Query.astroliftMyPermissions": "self-service: the caller's own effective permissions",
    "Query.astroliftNavTree": "self-service: the shape of the caller's own org for the sidebar",
    "Query.astroliftActiveIdentityProvider": "self-service: the IdP the sign-in page renders",
    "Query.astroliftActiveSessions": "self-service: the caller's own sessions",
    "Query.astroliftElevationStatus": "self-service: the caller's own step-up state",
    "Query.astroliftMyNotifications": "self-service: the caller's own notifications",
    "Query.astroliftMyMobileDevices": "self-service: the caller's own push registrations",
    "Query.astroliftMyNotificationPreferences": "self-service: the caller's own notification preferences",
    "Query.astroliftMyDevices": "self-service: the caller's own push devices",
    "Query.astroliftMyAlertSubscriptions": "self-service: the caller's own alert subscriptions",
    "Query.astroliftMyApps": "self-service: the apps the caller's own bindings reach",
    "Query.astroliftMyAppsPage": "self-service: the apps the caller's own bindings reach, paginated",
    "Query.assignableAstroliftProjects": "self-service: the projects the caller's own bindings let them assign apps to",
    "Query.astroliftMyConnectedAccounts": "self-service: the caller's own per-user source connections",
    "Query.astroliftMyUiPreferences": "self-service: the caller's own UI preferences plus the active org's one default",
    "Mutation.updateMyProfile": "self-service: edits the caller's own profile",
    "Mutation.updateMyUiPreferences": "self-service: saves the caller's own UI preferences, keyed on the viewer",
    "Mutation.generateInstallEnrollmentQr": "self-service: enrolls the caller's own mobile device",
    "Mutation.logoutAllSessions": "self-service: revokes the caller's own sessions",
    "Mutation.heartbeatSession": "self-service: the caller's own session liveness ping",
    "Mutation.elevateAdminSession": "self-service: step-up of the caller's own session",
    "Mutation.deelevateAdminSession": "self-service: drops the caller's own step-up",
    "Mutation.requestAttestationChallenge": "self-service: a device attestation nonce for the caller's own session",
    "Mutation.attestSession": "self-service: attests the caller's own session",
    "Mutation.assertSession": "self-service: asserts the caller's own attested session",
    "Mutation.markNotificationRead": "self-service: the caller's own notification",
    "Mutation.markAllNotificationsRead": "self-service: the caller's own notifications",
    "Mutation.registerMobileDevice": "self-service: registers a push device on the caller's own account",
    "Mutation.revokeMobileDevice": "self-service: revokes one of the caller's own push devices",
    "Mutation.setNotificationPreference": "self-service: one of the caller's own notification preferences",
    "Mutation.astroliftConnectUserSourceProvider": "self-service: the caller's own per-user source connection",
    "Mutation.astroliftDisconnectUserSourceProvider": "self-service: the caller's own per-user source connection",
    "Subscription.notificationReceived": "self-service: the caller's own notifications as they arrive",
    # -- Install-wide: the platform operator, checked first in the body (#1978) --
    "Mutation.createOrganization": "install-wide: creates a tenant, so no tenant context exists to scope on",
    "Query.dispatchers": "install-wide: the dispatcher fleet spans tenants; the platform operator's alone",
    "Query.scanCloudOrphans": "install-wide: orphans have no owning tenant; the platform operator's alone",
    "Mutation.reapCloudOrphan": "install-wide: orphans have no owning tenant; the platform operator's alone",
    "Mutation.resyncAllAstroliftCiWorkflows": "install-wide: sweeps every org; the platform operator's alone",
    "Mutation.setFeatureFlag": "install-wide: a platform setting; the platform operator's alone",
    "Mutation.createWorkflowDefinition": "install-wide: legacy workflow definitions carry no org; the operator's alone",
    "Mutation.createWorkflowTrigger": "install-wide: legacy workflow definitions carry no org; the operator's alone",
    "Mutation.overrideWorkflowState": "install-wide: a superuser's override of a legacy workflow instance",
    # -- Gates that depend on the row, checked in the body --------------------
    "Mutation.cancelWorkflowInstance": "workflow.trigger at the run's own app, project or org, or the operator (#1965)",
    "Mutation.terminateWorkflowInstance": "workflow.trigger at the run's own app, project or org, or the operator (#1965)",
    "Mutation.signalWorkflowInstance": "workflow.trigger at the run's own app, project or org, or the operator (#1965)",
    "Mutation.submitForm": "a public form takes anonymous submissions; a private one checks form.submit in the body",
    "Query.astroliftRunAudit": (
        "each run kind is read under its own permission and narrowed to the caller's scopes in "
        "run_audit_page, which refuses a caller with no run read at all"
    ),
    "Mutation.revokeAstroliftSession": (
        "own session: self-service; another user's: org.manage_members at the explicit org scope, in the body"
    ),
    "Mutation.astroliftAnonymizeUser": (
        "self: self-service; another user: org.manage_members at the explicit org scope plus the grant ceiling, "
        "in the body"
    ),
    # -- Operator console: an active staff session plus Django model perms ----
    "/app/nested_admin/^server-data\\.js$": "operator console: nested-admin lookup URLs, refuses anyone but active staff",
    "/app/core/core/user-permissions-report/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/user-permissions-tree-view/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/compare-user-permissions/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/compare-user-permissions-data/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/search-users/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/toggle-group-membership/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/search-groups/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/compare-group-permissions-data/": "operator console: admin_tooling (staff session + Django model perms)",
    "/app/core/core/toggle-permission-in-group/": "operator console: admin_tooling (staff session + Django model perms)",
    # -- GraphQL transports: every field is walked on its own above ----------
    "/app/gql/config/": "GraphQL transport; each Query and Mutation field is checked on its own",
    "/app/gql/config/ws/": "GraphQL WebSocket transport; each Subscription field is checked on its own",
    "/app/gql/config/auth/": "the login-only schema; its three fields are checked on their own",
    # -- Webhooks that verify their own signature ----------------------------
    "/api/webhooks/workflow/<str:org_slug>/<slug:slug>": "webhook: the trigger's own signing secret, bound to one org's trigger",
    "/api/webhooks/github/<str:app_guid>/": "webhook: HMAC with the app's webhook secret",
    "/app/auth1/scm/github/webhook/<str:connection_id>/": "webhook: HMAC with the connection's webhook secret",
    "/app/auth1/scm/gitlab/webhook/<str:connection_id>/": "webhook: the connection's webhook token",
    "/webhooks/pipelines/github/<str:org_slug>/": "webhook: HMAC with the org's pipeline webhook secret",
    "/webhooks/pipelines/gitlab/<str:org_slug>/": "webhook: the org's pipeline webhook token",
    "/app/webhooks/ses-events/": "webhook: SNS message signature from the platform's SES topic",
    # -- Machine credentials bound to one org, app, cluster or object ---------
    "/api/dispatch/v1/register/": "machine: DISPATCHER_BOOTSTRAP_TOKEN bearer; mints a key bound to the named org",
    "/api/clusters/v1/<str:cluster_guid>/heartbeat/": "machine: the in-cluster agent key bound to this cluster",
    "/api/clusters/v1/<str:cluster_guid>/model-test-result/": "machine: the in-cluster agent key bound to this cluster",
    "/api/pipelines/v1/runners/register/": "machine: RUNNER_REGISTRATION_TOKEN bearer; mints a runner key for the org",
    "/api/pipelines/v1/runners/heartbeat/": "machine: the runner key, bound to its org",
    "/api/pipelines/v1/runners/claim/": "machine: the runner key, bound to its org",
    "/api/pipelines/v1/runners/<str:runner_guid>/jobs/<str:job_run_guid>/complete/": (
        "machine: the runner key, bound to its org and its own jobs"
    ),
    "/api/cli/v1/apps/<str:app_slug>/deploy/": "machine: an alft_dt_ deploy token bound to this one app",
    "/api/cli/v1/deployments/<str:guid>/status/": "machine: an alft_dt_ deploy token bound to the deployment's app",
    "/api/cli/v1/apps/<str:app_slug>/static/<str:workload>/upload/": "machine: an alft_dt_ deploy token bound to this app",
    "/api/edge/v1/domain-handoff/exchange/": "machine: a single-use handoff token bound to one hostname",
    "/api/scim/v2/Users": "machine: an org's alft_st_ SCIM token, confined to that org",
    "/api/scim/v2/Users/<str:member_guid>": "machine: an org's alft_st_ SCIM token, confined to that org",
    "/app/audit_exports/<str:guid>/<str:token>/": "download link: the export's own token, hashed at rest, with an expiry",
    "/app/app_log_exports/<str:guid>/<str:token>/": "download link: the export's own token, hashed at rest, with an expiry",
}

# ---------------------------------------------------------------------------
# Tracked gaps outside the agent surfaces: one issue per area, each listing
# every route here and what it checks today.
# ---------------------------------------------------------------------------

GAPS: dict[str, tuple[str, ...]] = {
    # TODO(#2103): identity routes without a scoped gate; the issue says what each checks today.
    "#2103": (
        "Query.astroliftOrganization",
        "Query.astroliftTeamSlugAvailable",
        "Query.astroliftProjectSlugAvailable",
        "Query.astroliftMembers",
        "Query.astroliftMembersPage",
        "Query.astroliftTeamMembers",
        "Query.astroliftOrgMembersForApprovalPicker",
        "Query.astroliftInvitations",
        "Query.astroliftInvitationsPage",
        "Query.astroliftRoles",
        "Query.astroliftRolesPage",
        "Query.astroliftSearchableUsers",
        "Query.astroliftRolesICanGrant",
        "Query.astroliftRoleBindings",
        "Query.astroliftRoleBindingsPage",
        "Query.astroliftApiTokens",
        "Query.astroliftApiTokensPage",
        "Query.astroliftPolicies",
        "Query.astroliftPoliciesPage",
        "Query.astroliftOrganizationAllowlistDomains",
        "Query.astroliftIdentityProviders",
        "Mutation.updateOrganization",
        "Mutation.softDeleteOrganization",
        "Mutation.createTeam",
        "Mutation.updateTeam",
        "Mutation.softDeleteTeam",
        "Mutation.createProject",
        "Mutation.updateProject",
        "Mutation.softDeleteProject",
        "Mutation.grantRole",
        "Mutation.revokeRoleBinding",
        "Mutation.bulkRevokeAstroliftRoleBindings",
        "Mutation.bulkAssignAstroliftTeamMemberRoles",
        "Mutation.createRole",
        "Mutation.updateRole",
        "Mutation.softDeleteRole",
        "Mutation.createInvitation",
        "Mutation.revokeInvitation",
        "Mutation.deleteInvitation",
        "Mutation.resendInvitation",
        "Mutation.addOrganizationAllowlistDomain",
        "Mutation.removeOrganizationAllowlistDomain",
        "Mutation.setOrganizationModule",
        "Mutation.createApiToken",
        "Mutation.revokeApiToken",
        "Mutation.createPolicy",
        "Mutation.updatePolicy",
        "Mutation.softDeletePolicy",
        "Mutation.createIdentityProvider",
        "Mutation.updateIdentityProvider",
        "Mutation.setActiveIdentityProvider",
        "Mutation.softDeleteIdentityProvider",
        "Mutation.markOnboardingComplete",
        "Query.astroliftRole",
        "Query.astroliftPolicy",
        "Query.astroliftGroupRoleMappingsPage",
        "Query.astroliftAccessOn",
        "Query.astroliftPrincipalSearch",
        "Query.astroliftGrantPreview",
        "Query.astroliftPolicySimulation",
        "Query.astroliftPolicyConditionCatalog",
        "Query.astroliftApiTokenScopeCatalog",
        "Mutation.updateRoleBinding",
        "Mutation.createGroupRoleMapping",
        "Mutation.deleteGroupRoleMapping",
    ),
    # TODO(#2104): lifecycle routes without a scoped gate; the issue says what each checks today.
    "#2104": (
        "Query.astroliftEnvironments",
        "Query.astroliftDeployments",
        "Query.astroliftDeploymentsPage",
        "Query.astroliftDeployment",
        "Query.astroliftDeploymentApprovalHistory",
        "Query.astroliftDeploymentReleaseNotes",
        "Query.astroliftDeploymentLog",
        "Query.astroliftScheduledJobRuns",
        "Query.astroliftScheduledJobRunsPage",
        "Query.astroliftScheduledJobRun",
        "Query.astroliftCommandRuns",
        "Query.astroliftCommandRunsPage",
        "Query.astroliftCommandRun",
        "Query.astroliftPreviewEnvironments",
        "Query.astroliftPreviewEnvironmentsPage",
        "Query.astroliftAppDomains",
        "Query.astroliftAppPods",
        "Query.agentBoxPods",
        "Query.astroliftWorkloadPodStatusBreakdown",
        "Query.astroliftAppDeployTokens",
        "Query.astroliftAppDeployTokensPage",
        "Query.astroliftAppDnsRecords",
        "Query.astroliftAppCertificates",
        "Query.astroliftAppIdentityBinding",
        "Query.previewAstroliftDeregister",
        "Query.previewAstroliftForceRedeploy",
        "Query.astroliftTaskRuns",
        "Query.astroliftTaskRunsPage",
        "Query.astroliftTaskRun",
        "Query.astroliftAgentRuns",
        "Query.astroliftAgentRunsPage",
        "Mutation.startDeployment",
        "Mutation.approveDeployment",
        "Mutation.rejectDeployment",
        "Mutation.bulkApproveDeployments",
        "Mutation.bulkRejectDeployments",
        "Mutation.abortDeployment",
        "Mutation.deleteDeployment",
        "Mutation.rollbackDeployment",
        "Mutation.redeployApp",
        "Mutation.promoteDeployment",
        "Mutation.pauseEnvironment",
        "Mutation.resumeEnvironment",
        "Mutation.pauseAppIngress",
        "Mutation.resumeAppIngress",
        "Mutation.tearDownPreview",
        "Mutation.extendPreviewTtl",
        "Mutation.setPreviewPinned",
        "Mutation.createPreviewEnvironment",
        "Mutation.migrateAppToCluster",
        "Mutation.addAppDomain",
        "Mutation.addWildcardDomain",
        "Mutation.removeAppDomain",
        "Mutation.recheckDomainValidation",
        "Mutation.uploadCustomDomainCertificate",
        "Mutation.setDomainRedirects",
        "Mutation.setDomainPathRoutes",
        "Mutation.createDeployToken",
        "Mutation.rotateDeployToken",
        "Mutation.revokeDeployToken",
        "Mutation.deleteAppDnsRecord",
        "Mutation.revokeAppCertificate",
        "Mutation.deleteAppIdentityRole",
        "Mutation.archiveAppRegistryRepo",
        "Mutation.deleteAppIngress",
        "Mutation.triggerAstroliftDeployWorkflow",
        "Mutation.restartAstroliftWorkload",
        "Mutation.scaleAstroliftWorkload",
        "Mutation.installAstroliftSourceWebhook",
        "Mutation.pushAstroliftCiSecretsToRepo",
        "Mutation.validateAstroliftCiSecrets",
        "Mutation.pushAstroliftCiWorkflowToRepo",
        "Mutation.retryAstroliftAutowire",
        "Mutation.deregisterAstroliftApp",
        "Mutation.cancelAstroliftDeregister",
        "Mutation.forceAstroliftRedeploy",
        "Mutation.runAstroliftJobOnce",
        "Mutation.rerunAstroliftOnboarding",
        "Mutation.setEnvironmentSetting",
        "Mutation.clearEnvironmentSetting",
        "Mutation.runTask",
        "Subscription.astroliftDeploymentLifecycleStream",
        "/api/builder/v1/dev-environments/",
        "/api/builder/v1/dev-environments/<str:guid>/files/",
        "/api/builder/v1/dev-environments/<str:guid>/promote/",
        "Query.astroliftEnvironmentsPage",
        "Query.astroliftPreviewEnvironmentCounts",
    ),
    # TODO(#2105): registry routes without a scoped gate; the issue says what each checks today.
    "#2105": (
        "Query.astroliftApp",
        "Query.astroliftAppTeamAccesses",
        "Query.astroliftAppTeamAccessesPage",
        "Query.astroliftWorkloads",
        "Query.astroliftWorkloadsPage",
        "Query.astroliftWorkload",
        "Query.astroliftWorkloadScalingStatus",
        "Query.astroliftContainers",
        "Query.astroliftRenderedManifest",
        "Query.astroliftWorkloadManifest",
        "Query.astroliftAppDoctor",
        "Query.scanAppManifests",
        "Mutation.registerApp",
        "Mutation.registerAgentRepo",
        "Mutation.registerAppRepo",
        "Mutation.updateApp",
        "Mutation.setAppSubdomain",
        "Mutation.softDeleteApp",
        "Mutation.tearDownApp",
        "Mutation.transferApp",
        "Mutation.updateManifest",
        "Mutation.applyStagedManifest",
        "Mutation.syncManifestFromRepo",
        "Mutation.pushManifestToRepo",
        "Mutation.moveAppToTeam",
        "Mutation.grantTeamAccessToApp",
        "Mutation.revokeTeamAccessFromApp",
        "Mutation.resyncAstroliftManifestFromRepo",
        "Mutation.assignAstroliftAppToProject",
        "Mutation.updateAstroliftSecurityPolicy",
        "Mutation.pauseAstroliftAppWebhookDeploys",
        "Mutation.resumeAstroliftAppWebhookDeploys",
        "Mutation.setRetentionPolicy",
        "Mutation.archiveApp",
        "Mutation.restoreApp",
        "Query.astroliftAppAccess",
        "Query.astroliftAppAccessPreview",
        "Mutation.setAppAccess",
    ),
    # TODO(#2106): managed services routes without a scoped gate; the issue says what each checks today.
    "#2106": (
        "Query.astroliftProjectResourceClusters",
        "Query.astroliftProjectManagedServiceCatalog",
        "Query.astroliftProjectManagedServices",
        "Query.astroliftProjectSecretBundles",
        "Query.astroliftAppSecrets",
        "Query.astroliftAppSecretHistory",
        "Query.astroliftSecretBundles",
        "Query.astroliftAppSecretBundleAttachments",
        "Query.astroliftManagedServices",
        "Query.astroliftManagedServicesPage",
        "Query.astroliftManagedServiceCostPreview",
        "Query.astroliftWorkloadIdentityGrants",
        "Query.astroliftManagedServiceObjects",
        "Query.astroliftManagedServiceQueueDepth",
        "Query.astroliftEmailServiceDetail",
        "Query.astroliftEmailTemplates",
        "Query.astroliftEmailTemplate",
        "Query.astroliftEmailTemplateStats",
        "Query.astroliftEmailMessages",
        "Query.astroliftEmailEngagementMetrics",
        "Query.astroliftSecretChangeProposals",
        "Query.astroliftSecretChangeProposal",
        "Mutation.setAppSecret",
        "Mutation.rotateAppSecret",
        "Mutation.deleteAppSecret",
        "Mutation.setAppSecretMetadata",
        "Mutation.bulkImportAppSecrets",
        "Mutation.revealAppSecret",
        "Mutation.createProjectSecretBundle",
        "Mutation.updateProjectSecretBundle",
        "Mutation.deleteProjectSecretBundle",
        "Mutation.setProjectBundleSecretValue",
        "Mutation.deleteProjectBundleSecretValue",
        "Mutation.revealProjectBundleSecretValue",
        "Mutation.attachSecretBundle",
        "Mutation.detachSecretBundle",
        "Mutation.rotateSecretBundle",
        "Mutation.provisionProjectManagedService",
        "Mutation.attachProjectManagedService",
        "Mutation.detachProjectManagedService",
        "Mutation.updateProjectManagedService",
        "Mutation.reprovisionProjectManagedService",
        "Mutation.deprovisionProjectManagedService",
        "Mutation.provisionManagedService",
        "Mutation.updateManagedService",
        "Mutation.reprovisionManagedService",
        "Mutation.deprovisionManagedService",
        "Mutation.adoptManagedResource",
        "Mutation.revealManagedServiceConnection",
        "Mutation.testModelEndpoint",
        "Mutation.sendManagedServiceTestEmail",
        "Mutation.addEmailSuppressionEntry",
        "Mutation.removeEmailSuppressionEntry",
        "Mutation.createEmailTemplate",
        "Mutation.updateEmailTemplate",
        "Mutation.deleteEmailTemplate",
        "Mutation.proposeSecretChange",
        "Mutation.approveSecretChange",
        "Mutation.rejectSecretChange",
        "Mutation.withdrawSecretChange",
        "astrolift_list_project_resource_clusters",
        "astrolift_list_project_resource_catalog",
        "astrolift_list_project_resources",
        "astrolift_preview_project_resource_cost",
        "astrolift_provision_project_resource",
        "astrolift_attach_project_resource",
        "astrolift_detach_project_resource",
        "astrolift_update_project_resource",
        "astrolift_reprovision_project_resource",
        "astrolift_deprovision_project_resource",
    ),
    # TODO(#2107): operations routes without a scoped gate; the issue says what each checks today.
    "#2107": (
        "Query.astroliftAppUptime",
        "Query.astroliftEvents",
        "Query.astroliftEventsAggregated",
        "Query.astroliftEventsAggregatedPage",
        "Query.astroliftEventsPage",
        "Query.astroliftRecentActivity",
        "Query.astroliftAuditEvents",
        "Query.astroliftAuditEventsPage",
        "Query.astroliftAuditRetention",
        "Query.astroliftObservabilityRetention",
        "Query.astroliftZentinelleConnection",
        "Query.astroliftWebhookSubscriptions",
        "Query.astroliftWebhookSubscriptionsPage",
        "Query.astroliftWebhookDeliveries",
        "Query.astroliftWebhookDeliveriesPage",
        "Query.astroliftAlertRules",
        "Query.astroliftAlertRulesPage",
        "Query.astroliftAppMetrics",
        "Query.astroliftAlertEvents",
        "Query.astroliftAlertEventsPage",
        "Mutation.createWebhookSubscription",
        "Mutation.updateWebhookSubscription",
        "Mutation.deleteWebhookSubscription",
        "Mutation.testWebhookSubscription",
        "Mutation.rotateOutboundWebhookSecret",
        "Mutation.testNotificationChannel",
        "Mutation.setNotificationProfile",
        "Mutation.createAlertRule",
        "Mutation.updateAlertRule",
        "Mutation.deleteAlertRule",
        "Mutation.acknowledgeAlertEvent",
        "Mutation.muteAlertRule",
        "Mutation.unmuteAlertRule",
        "Mutation.exportAuditEvents",
        "Mutation.exportAstroliftAppLogs",
        "Mutation.setAlertSubscription",
        "Mutation.clearAlertSubscription",
        "Mutation.bulkRollingRestart",
        "Mutation.bulkPushSecrets",
        "Mutation.bulkResyncManifest",
        "Mutation.placeObservabilityRetentionHold",
        "Mutation.releaseObservabilityRetentionHold",
        "Mutation.connectZentinelle",
        "Mutation.disconnectZentinelle",
        "Mutation.registerZentinelleCluster",
        "Mutation.setZentinelleGatewayEnabled",
        "Mutation.rotateZentinelleGatewayCredential",
        "Mutation.unregisterZentinelleCluster",
    ),
    # TODO(#2108): clusters routes without a scoped gate; the issue says what each checks today.
    "#2108": (
        "Query.astroliftClusters",
        "Query.astroliftClustersPage",
        "Query.astroliftClusterCount",
        "Query.astroliftManagedDomains",
        "Query.astroliftProviderRegions",
        "Query.astroliftClusterLifecycleAudit",
        "Query.astroliftRecentClusterWorkflows",
        "Query.astroliftAppCountForCluster",
        "Query.astroliftClusterHealth",
        "Query.astroliftClusterLiveState",
        "Query.astroliftCognitoUserPools",
        "Query.astroliftCognitoUserPoolClients",
        "Query.astroliftClusterBootstrapPlan",
        "Query.astroliftClusterPrometheusMetrics",
        "Query.astroliftClusterPrometheusRangeMetrics",
        "Query.astroliftClusterSystemMetrics",
        "Query.astroliftClusterWorkloadHealth",
        "Query.astroliftClusterCertificates",
        "Query.astroliftDnsZones",
        "Query.astroliftDnsCertificates",
        "Mutation.registerTenantCluster",
        "Mutation.issueClusterAgentKey",
        "Mutation.deployClusterAgent",
        "Mutation.updateTenantCluster",
        "Mutation.reconcileClusterIngresses",
        "Mutation.bringClusterIntoManagement",
        "Mutation.refreshClusterManagement",
        "Mutation.decommissionCluster",
        "Mutation.installClusterPrereqs",
        "Mutation.recordClusterBootstrapRun",
        "Mutation.unregisterTenantCluster",
        "Mutation.createManagedDomain",
        "Mutation.updateManagedDomain",
        "Mutation.softDeleteManagedDomain",
        "Mutation.verifyManagedDomain",
        "Mutation.provisionManagedDomain",
        "Mutation.revalidateManagedDomain",
        "Mutation.reissueManagedDomainCert",
        "Mutation.configureProviderPlugin",
        "Query.astroliftCluster",
        "Query.astroliftClusterAuthUsers",
        "Mutation.createClusterAuthUser",
        "Mutation.setClusterAuthUserPassword",
        "Mutation.resetClusterAuthUserPassword",
        "Mutation.setClusterAuthUserEnabled",
        "Mutation.deleteClusterAuthUser",
        "Mutation.setClusterAuthUserGroups",
        "Mutation.createClusterAuthGroup",
    ),
    # TODO(#2109): SCM routes without a scoped gate; the issue says what each checks today.
    "#2109": (
        "Query.astroliftSourceConnections",
        "Query.astroliftSourceConnectionsPage",
        "Query.astroliftSshDeployKeys",
        "Query.astroliftSshDeployKeysPage",
        "Query.astroliftAvailableRepos",
        "Query.astroliftSourceFile",
        "Mutation.connectSource",
        "Mutation.connectExistingGithubApp",
        "Mutation.updateSourceConnection",
        "Mutation.disconnectSource",
        "Mutation.generateSshDeployKey",
        "Mutation.rotateWebhookSecret",
        "Mutation.installScmWebhook",
        "Mutation.deleteSshDeployKey",
        "Mutation.pushCiWorkflow",
        "Mutation.resyncAstroliftCiWorkflow",
        "Mutation.pullCiWorkflowFromRepo",
        "Mutation.adoptRepoCiWorkflow",
        "Mutation.refreshCiWorkflowSyncStatus",
        "Mutation.openCiWorkflowReconcilePr",
        "/app/auth1/scm/github/start",
        "/app/auth1/scm/github/callback",
        "/app/auth1/scm/github/app-manifest/start",
        "/app/auth1/scm/github/app-manifest/callback",
        "/app/auth1/scm/github/app-manifest/setup",
        "/app/auth1/scm/gitlab/start",
        "/app/auth1/scm/gitlab/callback",
    ),
    # TODO(#2110): core and legacy boilerworks routes without a scoped gate; the issue says what each checks today.
    "#2110": (
        "Query.effectivePermissions",
        "Query.permissionDiagnose",
        "Query.permissionCompare",
        "Query.auditLogs",
        "Query.auditLogsPage",
        "Query.organization",
        "Query.organizations",
        "Query.organizationsPage",
        "Query.members",
        "Query.membersPage",
        "Mutation.switchUser",
        "Mutation.upsertUser",
        "Mutation.profile",
        "Mutation.profileRequestPwdChange",
        "Mutation.profileRequestDeleteUser",
        "Mutation.pinUpdate",
        "Mutation.pinTransaction",
        "Mutation.signRequestUser",
        "Mutation.signRequestSign",
        "Mutation.signRequestCancel",
        "Mutation.notification",
        "Mutation.notificationRead",
        "Mutation.permissionGroupOperation",
        "Mutation.libraryMkdir",
        "Mutation.libraryRmdir",
        "Mutation.libraryRenameDir",
        "Mutation.libraryRenameFile",
        "Mutation.libraryRmFile",
        "Mutation.librarySetIcon",
        "Mutation.generateRocketChatToken",
        "Mutation.preSignedUrlImageUpload",
        "Mutation.confirmPreSignedUrlImageUpload",
        "Mutation.uploadTextFile",
        "Mutation.processFile",
        "Mutation.fileUpload",
        "Mutation.profileImageFieldUpload",
        "Mutation.delete",
        "Mutation.activate",
        "Mutation.organization",
        "Mutation.upsertOrganization",
        "Mutation.organizationMemberStatus",
        "/app/sentry-debug/",
        "/app/export/",
        "/app/test/open_telemetry/",
        "/api/support/v1/tickets/",
        "/app/metrics/",
    ),
    # TODO(#2111): observability routes without a scoped gate; the issue says what each checks today.
    "#2111": (
        "Query.PostgresMetrics",
        "Query.ObjectStoreMetrics",
        "Query.ModelEndpointMetrics",
        "Query.astroliftAppGoldenSignals",
        "Query.astroliftAppStatusCodeBreakdown",
        "Query.astroliftWorkloadResourceUsage",
        "Query.astroliftAppUrlHealth",
        "Query.astroliftAppUrlProbeHistory",
        "Query.astroliftAppManagedServiceMetrics",
        "Query.astroliftPodResourceUsage",
        "Query.astroliftAppMetricNames",
        "Query.astroliftExecutePromql",
        "Query.astroliftAppTraces",
        "Query.astroliftTraceSpans",
        "Query.astroliftAppEndpointMetrics",
        "Query.astroliftAppLogs",
    ),
    # TODO(#2112): forms routes without a scoped gate; the issue says what each checks today.
    "#2112": (
        "Query.formDefinitions",
        "Query.formDefinition",
        "Query.formSubmissions",
        "Mutation.createFormDefinition",
        "Mutation.updateFormDefinition",
        "Mutation.publishForm",
        "Mutation.archiveForm",
        "Mutation.deleteFormDefinition",
        "Mutation.updateSubmissionStatus",
        "Subscription.formSubmissionReceived",
    ),
    # TODO(#2113): billing routes without a scoped gate; the issue says what each checks today.
    "#2113": (
        "Query.astroliftQuotas",
        "Query.astroliftQuotaUsageHistory",
        "Query.astroliftBudgets",
        "Query.astroliftCostSnapshots",
        "Query.astroliftCostTrend",
        "Query.astroliftCostForecast",
        "Query.astroliftCostByBinding",
        "Mutation.requestQuotaIncrease",
    ),
    # TODO(#2114): workflows routes without a scoped gate; the issue says what each checks today.
    "#2114": (
        "Query.previewWorkflowManifest",
        "Mutation.importWorkflowFlow",
        "Mutation.importWorkflowManifest",
    ),
    # TODO(#2115): pipelines routes without a scoped gate; the issue says what each checks today.
    "#2115": ("Mutation.createPipeline",),
}


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Surface:
    """One route of one surface, as the enforcement table lists it."""

    kind: str
    key: str
    area: str
    permission: str
    scope: str
    problems: tuple[str, ...]


def _codes() -> tuple[set, set]:
    """The code objects of the real gate wrappers, sync and async-generator."""
    from core.decorators import tenant_scoped
    from core.permissions import Permission, require_permission

    def sync(*args, **kwargs):
        return None

    async def agen(*args, **kwargs):
        yield None

    tenant = {tenant_scoped()(sync).__code__, tenant_scoped()(agen).__code__}
    permission = {
        require_permission(Permission.ORG_READ)(sync).__code__,
        require_permission(Permission.ORG_READ)(agen).__code__,
    }
    return tenant, permission


def _chain(fn: Any) -> Iterator[Any]:
    seen: set[int] = set()
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        yield fn
        fn = getattr(fn, "__wrapped__", None)


def _area(fn: Any) -> str:
    """The top-level package of the innermost function: the owning app."""
    innermost = None
    for innermost in _chain(fn):  # noqa: B007 — the last layer is the one wanted
        pass
    return (getattr(innermost, "__module__", "") or "").split(".")[0]


def _factory_name(factory: Callable) -> str:
    qualname = getattr(factory, "__qualname__", repr(factory))
    return qualname.replace(".<locals>._scope", "").replace(".<locals>.", ".")


def _probe(factory: Callable, org_id: int) -> str:
    """Why ``factory`` is unsafe on a miss, or "" when it names a scope."""
    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org_id)):
        try:
            answer = factory({})
        except Exception as exc:  # noqa: BLE001 — the reason is the finding
            return f"scope factory {_factory_name(factory)} raised on a miss ({type(exc).__name__})"
    if answer is None:
        return f"scope factory {_factory_name(factory)} answers None on a miss"
    return ""


def _gates(resolver: Any, org_id: int) -> tuple[str, str, tuple[str, ...]]:
    """``(permission, scope, problems)`` for one resolver's gate stack."""
    tenant_codes, permission_codes = _codes()
    layers = list(_chain(resolver))
    gates = [
        layer.__astrolift_permission_gate__
        for layer in layers
        if getattr(layer, "__code__", None) in permission_codes
    ]
    problems: list[str] = []
    if not gates:
        problems.append("no @require_permission")
    if not any(getattr(layer, "__code__", None) in tenant_codes for layer in layers):
        problems.append("no @tenant_scoped")
    scopes = []
    for gate in gates:
        if gate.any_scope:
            scopes.append("any scope, rows narrowed")
        elif gate.scope is None:
            scopes.append("targetless (selected team/project, else org)")
            problems.append(f"{'+'.join(p.value for p in gate.permissions)} is targetless")
        else:
            scopes.append(_factory_name(gate.scope))
            miss = _probe(gate.scope, org_id)
            if miss:
                problems.append(miss)
    permission = "; ".join("+".join(p.value for p in gate.permissions) for gate in gates)
    return permission, "; ".join(scopes), tuple(problems)


def _graphql(org_id: int) -> Iterator[Surface]:
    from config.schema import schema, schema_auth

    for prefix, served in (("", schema), ("auth:", schema_auth)):
        for kind in ("Query", "Mutation", "Subscription"):
            root = getattr(served._schema, f"{kind.lower()}_type")
            if root is None:
                continue
            for name in root.fields:
                field = served.get_field_for_type(field_name=name, type_name=root.name)
                resolver = field.base_resolver.wrapped_func if field.base_resolver is not None else None
                permission, scope, problems = _gates(resolver, org_id)
                yield Surface(
                    kind="graphql",
                    key=f"{prefix}{kind}.{name}",
                    area=_area(resolver),
                    permission=permission,
                    scope=scope,
                    problems=problems,
                )


def _declared(callback: Any):
    for layer in _chain(callback):
        declared = getattr(layer, "__astrolift_route_auth__", None)
        if declared is not None:
            return declared
    return None


def _admin_view_code():
    """The code object of ``AdminSite.admin_view``'s wrapper, which refuses
    anyone but an active staff member before the view runs."""
    from django.contrib import admin

    return admin.site.admin_view(lambda request: None).__code__


def _behind_admin_view(callback: Any, admin_code) -> bool:
    for layer in _chain(callback):
        if getattr(layer, "__code__", None) is admin_code:
            return True
        # ``AdminSite.get_urls`` / ``ModelAdmin.get_urls`` defer the wrap to
        # call time through a closure; Django marks those wrappers.
        if getattr(layer, "admin_site", None) is not None or getattr(layer, "model_admin", None) is not None:
            return True
    return False


def _urls() -> Iterator[Surface]:
    from django.urls import URLPattern, URLResolver, get_resolver

    def walk(patterns, prefix: str) -> Iterator[tuple[str, str | None, Any]]:
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
            elif isinstance(pattern, URLPattern):
                yield prefix + str(pattern.pattern), pattern.name, pattern.callback

    admin_code = _admin_view_code()
    admin_views = 0
    seen: set[str] = set()
    for route, name, callback in walk(get_resolver().url_patterns, "/"):
        if _behind_admin_view(callback, admin_code):
            admin_views += 1
            continue
        key = route if route not in seen else f"{route} ({name})"
        seen.add(key)
        declared = _declared(callback)
        yield Surface(
            kind="url",
            key=key,
            area=_area(callback),
            permission="+".join(p.value for p in declared.permissions) if declared else "",
            scope=f"{declared.credential}; {declared.scope}" if declared else "",
            problems=() if declared else ("no route_auth declaration",),
        )
    assert admin_views, "the Django admin views were not found; the admin recognition is broken"


def _websockets() -> Iterator[Surface]:
    from importlib import import_module

    from config.asgi import WEBSOCKET_ROUTES

    for prefix, module, handler in WEBSOCKET_ROUTES:
        view = getattr(import_module(module), handler)
        declared = _declared(view)
        yield Surface(
            kind="websocket",
            key=prefix,
            area=_area(view),
            permission="+".join(p.value for p in declared.permissions) if declared else "",
            scope=f"{declared.credential}; {declared.scope}" if declared else "",
            problems=() if declared else ("no route_auth declaration",),
        )


def _mcp(org_id: int) -> Iterator[Surface]:
    from astrolift_agents.mcp_contract import MCP_TOOL_META
    from astrolift_agents.views.mcp import _HANDLERS, ANY_SCOPE, TOOL_SCOPES

    for name, meta in MCP_TOOL_META.items():
        permissions = tuple(
            p for p in (meta.get("permissions") or (meta.get("permission"),)) if p is not None
        )
        problems: list[str] = []
        if name not in _HANDLERS:
            problems.append("no handler")
        if not meta.get("scope"):
            problems.append("no API-token scope")
        if not permissions:
            problems.append("no permission")
        declared = TOOL_SCOPES.get(name)
        if declared is None:
            scope = "targetless (the token's team, else org)"
            problems.append("no TOOL_SCOPES entry")
        elif declared == ANY_SCOPE:
            scope = "any scope, rows narrowed"
        else:
            scope = _factory_name(declared)
            miss = _probe(declared, org_id)
            if miss:
                problems.append(miss)
        yield Surface(
            kind="mcp",
            key=name,
            area=_area(_HANDLERS.get(name)),
            permission="+".join(p.value for p in permissions),
            scope=f"token scope {meta.get('scope')}; {scope}",
            problems=tuple(problems),
        )


def surfaces(org_id: int) -> list[Surface]:
    """The whole enforcement table: every route of every surface."""
    return [*_graphql(org_id), *_urls(), *_websockets(), *_mcp(org_id)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def table() -> list[Surface]:
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Guardrail", slug="guardrail-1866")
    return surfaces(org.pk)


def _tracked() -> dict[str, str]:
    tracked: dict[str, str] = {}
    for issue, entries in GAPS.items():
        for key in entries:
            assert key not in tracked, f"{key} is tracked twice"
            assert key not in ALLOWED, f"{key} is both allowed and tracked"
            tracked[key] = issue
    return tracked


def test_every_surface_declares_a_permission_and_its_scope(table) -> None:
    tracked = _tracked()
    failures = [
        f"{row.kind} {row.key}: {'; '.join(row.problems)}"
        for row in table
        if row.problems and row.key not in ALLOWED and row.key not in tracked
    ]
    assert not failures, (
        "These routes lack a permission + scope declaration:\n  - "
        + "\n  - ".join(failures)
        + "\n\nGive each one a scoped gate (@require_permission(..., scope=...|any_scope=True) + "
        "@tenant_scoped, route_auth for a REST or WebSocket view, a TOOL_SCOPES entry for an MCP "
        "tool). A route that is public by design goes on ALLOWED with its reason."
    )


def test_exemption_lists_hold_only_live_failing_routes(table) -> None:
    by_key = {row.key: row for row in table}
    stale = [key for key in [*ALLOWED, *_tracked()] if key not in by_key or not by_key[key].problems]
    assert not stale, (
        "These entries name a route that no longer exists or now passes; remove them:\n  - "
        + "\n  - ".join(stale)
    )


def test_gaps_reference_an_issue() -> None:
    for issue, entries in GAPS.items():
        assert issue.startswith("#") and issue[1:].isdigit(), issue
        assert entries, f"{issue} tracks nothing"


def test_the_login_schema_stays_login_only(table) -> None:
    auth = sorted(row.key for row in table if row.key.startswith("auth:"))
    assert auth == ["auth:Mutation.login", "auth:Mutation.logout", "auth:Query.ok"]


def _org_scope_factory(_args):
    from core.permissions import PermissionScope, ScopeKind

    return PermissionScope(kind=ScopeKind.ORG, id=1)


def _none_on_a_miss(_args):
    return None


def _look_alike(*permissions, **_kwargs):
    """Carries the real gate's marker, checks nothing."""
    import functools

    from core.permissions import PermissionGate

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper.__astrolift_permission_gate__ = PermissionGate(
            permissions=permissions, scope=None, any_scope=True
        )
        return wrapper

    return decorator


def _resolver(*decorators):
    def resolver(self, info, slug: str = ""):
        return None

    for decorate in reversed(decorators):
        resolver = decorate(resolver)
    return resolver


def _stream(*decorators):
    async def resolver(self, info, slug: str = ""):
        yield None

    for decorate in reversed(decorators):
        resolver = decorate(resolver)
    return resolver


def _synthetic():
    from core.decorators import tenant_scoped
    from core.permissions import Permission, require_permission

    read = Permission.AGENT_READ
    return {
        "scoped": (_resolver(require_permission(read, scope=_org_scope_factory), tenant_scoped()), ()),
        "collection": (_resolver(require_permission(read, any_scope=True), tenant_scoped()), ()),
        "subscription": (_stream(require_permission(read, scope=_org_scope_factory), tenant_scoped()), ()),
        "targetless": (_resolver(require_permission(read), tenant_scoped()), ("agent.read is targetless",)),
        "none on a miss": (
            _resolver(require_permission(read, scope=_none_on_a_miss), tenant_scoped()),
            ("scope factory _none_on_a_miss answers None on a miss",),
        ),
        "no tenant": (_resolver(require_permission(read, any_scope=True)), ("no @tenant_scoped",)),
        "look-alike": (_resolver(_look_alike(read), tenant_scoped()), ("no @require_permission",)),
    }


@pytest.mark.parametrize("case", list(_synthetic()))
def test_the_walk_reads_the_real_gates(case) -> None:
    """The guardrail's own red cases: it recognises the real decorators by
    their code, so a wrapper that forges the gate's marker does not pass,
    and it catches the targetless check and a factory that misses to None."""
    from astrolift_identity.models import Organization

    resolver, expected = _synthetic()[case]
    org = Organization.objects.create(name="Walk", slug=f"walk-{case.replace(' ', '-')}")
    assert _gates(resolver, org.pk)[2] == expected
