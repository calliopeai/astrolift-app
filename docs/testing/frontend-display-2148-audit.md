# Frontend display and logic checklist audit (#2148)

Audited against isolated base `a6e2260374d1ef8ec0c663e5a75d2090bf805c0c` plus this branch. The original 90-item issue was read in full. This document records each item without closing the issue or treating parked features as implemented.

**Fixed** means implemented here with the evidence named below. **Pre-existing** means current source already replaces or corrects the reported behavior, including earlier integrated display fixes. **Remaining** records outstanding behavior, contract or cleanup work. **External** is owned by another branch. **Parked** preserves the disabled Forms/Playground route contract.

This is an audit of current source and focused regressions, not a claim that every pre-existing behavior was independently rerun. Agent Host Protocol is excluded.

## Checks

Final focused verification: **44 React/Apollo checks across 10 files**, **31 Chromium behavior/layout checks**, Storybook production build, TypeScript and ESLint (zero errors; existing effect warnings). No backend/schema changes and no production writes. The parent runs the combined release checks after integration.

Feature commits: `4409a668`, `79a022b3`, `302113fd`, `917de312`.

- Focused cluster/config/operational/navigation React and real Apollo tests; terminal polling and actual concurrent mutations use the real Apollo client over a controlled transport.
- Chromium behavior checks cover actual menu admission, separate SVG paint servers, draft choice/readiness, pending rows, command target and uppercase-running input.
- Typecheck, ESLint and affected Storybook builds/layout checks; the parent runs the final combined route/build checks.

## Item evidence

| Item | Status | Current evidence / remaining work |
| --- | --- | --- |
| 1. AstroliftPolicy.conditions typed as object but used as an array | Fixed | The facade treats condition JSON as unknown input for the existing validated parser, allowing arrays and malformed legacy values without an object cast. Policy fixtures use actual arrays; TypeScript and 62 policy story renders pass. |
| 2. Cluster card view uses plain anchors and reloads the page | Pre-existing | Cluster cards use shared ListPage rowHref/navigation rather than a plain anchor. |
| 3. Full preflight refresh branch is unreachable | Fixed | Managed row menu exposes onRefresh(cluster,true); real Apollo input and Chromium menu checks. |
| 4. Organization settings Save is enabled with no changes | Pre-existing | OrganizationSettings passes generalDirty/retentionDirty/policiesDirty to independent save sections. |
| 5. Cluster status flashes 'No agent' cards while live state loads | Fixed | cluster-connectivity.test.tsx proves unknown/error states do not report No agent or mount provider query slots. |
| 6. Activity tab copy reads 'BringClusterInto- Management' | Pre-existing | Earlier display copy fix separates bring-into-management workflow words. |
| 7. Cluster settings allowlist reason omits the bootstrap history table | Pre-existing | frontend/bootstrap.md points at the exact allowlist; raw-table-allowlist.mjs now has an empty baseline. |
| 8. IngressAuth spinners are inconsistent between Save & Apply and Apply | Remaining | IngressAuth still spins Save & Apply on busy and Apply only on reconciling. |
| 9. Agent wizard steps are drifting copies of the apps wizard steps | Remaining | AgentReviewSubmitStep still declares local SideEffectStep/StepStatus; wizard copies remain separate. |
| 10. Manifest preview Retry never shows its success or failure toast | Pre-existing | useManifestPreviewStep.auto now takes no options; retry reports its result in the inline banner, without a toast contract. |
| 11. ObservabilitySection doc comment names the wrong link target | Pre-existing | Earlier observability documentation-link correction is included in the audited base. |
| 12. CI reference workflow hard-codes aws-region us-west-2 | External | Parent/forms agent owns canonical CI region and CiSetup work; requires their integration, not edits in this branch. |
| 13. Latest deployment badge shows raw status strings | Pre-existing | Old LatestDeploymentRow was replaced by LatestDeployPanel, which translates status keys. |
| 14. Workload row ready count never reflects real readiness | Fixed | workload-readiness.test.tsx and Chromium show unknown ready count, retaining only declared desired replicas. |
| 15. Empty image tag renders blank instead of falling back to the id | Pre-existing | Earlier blank image-tag fallback correction and regression are included in the audited base. |
| 16. URL card 'subdomain' field actually validates a full hostname | Pre-existing | use-url-card.ts separates short subdomain and backend-computed managedHostname. |
| 17. Manifest preview error line and column render 0 incorrectly | Pre-existing | Earlier manifest diagnostic zero-coordinate correction is included in the audited base. |
| 18. Code view 'services' pill never finds the managed services section | Pre-existing | Earlier managed_services section-link codec correction and story are included in the audited base. |
| 19. Conflict modal 'Force overwrite' does not save anything | Fixed | ConfigEditorClient regression keeps draft without a mutation; explicit Save submits later, including failed-save draft retention. |
| 20. Path route form cannot be submitted when no workloads exist | Pre-existing | Old path-route/workload form is absent; AddDomainSheet submits a hostname without an empty workload Select. |
| 21. Deploy token rotate confirm ignores the configured grace period | Remaining | DeployTokensScreen rotate confirmation still uses ROTATION_GRACE_DEFAULT_SECONDS; no pre-rotation configured grace read is wired. |
| 22. Secret expiry badge says 'Expired 1d ago' for keys expiring today | Pre-existing | Earlier future-under-a-day SecretExpiryBadge correction is included in the audited base. |
| 23. Secret sheets are not blocked while a rotate is in flight | Fixed | Real Apollo rotation request now contributes to useAppSecrets.busy for both shared sheets. |
| 24. Email detail sheet comment says five panels, it renders twelve | Pre-existing | EmailDetailSheet header documents five health panels and separately scoped embedded lists. |
| 25. Email sheet allowlist reason misdescribes its tables | Pre-existing | Email tables migrated to embedded ListPage; raw table allowlist is empty and bootstrap references its exact-match gate. |
| 26. Workload detail links use plain anchors and reload the page | Pre-existing | Earlier WorkloadLink navigation correction is included in the audited base. |
| 27. Danger zone deregister badge never shows before first open | Remaining | DangerZone still loads deregistration preview only after opening; pre-open magnitude is unavailable. |
| 28. Clearing an environment override has no busy state | Fixed | Environment/key-scoped pending admission survives the awaited refetch; the clear button disables and spins only its row. pending-operations.test.tsx covers duplicates, denial and refresh completion. |
| 29. Command runner Run does nothing when no container is selected | Fixed | Operational React and Chromium checks require a nonempty containerName before Run. |
| 30. Run command page highlights the Deployments tab | Fixed | Command runner fallback uses commands; canonical AppCommandsRedirect already resolves Logs & metrics Commands. |
| 31. Observability duplicates pod selection and ignores ?container= | Remaining | use-app-observability.ts still has KNOWN_SIDECARS/pickDefaultContainer and does not consume container deep-link state. |
| 32. Agent list item Pick omits fields the schema now provides | Remaining | Agent list facade omits run-spec baseline fields; AgentControl retains save-to-set fallback notices. Verify current reads before altering DTO. |
| 33. Run status spellings drift between terminal set and tones | Fixed | Terminal aliases are shared and case normalized; actual Apollo polling stops both task/log reads, canceled timeline is skipped. |
| 34. Agent and apps repo pickers link to different provider routes | Remaining | AgentRepoPickerStep points at settings/source-providers, app RepoPickerStep at providers#source; shared canonical picker route remains to reconcile. |
| 35. Run content comment disagrees with StatusCell badge variants | Pre-existing | AgentRunScreen now uses StatusDot/runDot; the obsolete outline/secondary status-badge comment is absent. |
| 36. Agent overview matches running status case-sensitively | Fixed | AgentOverview matches RUNNING case insensitively; focused React/Chromium overseer input checks. |
| 37. Overview run mode tile shows 'Service · Service' | Pre-existing | AgentOverview uses AgentFrame.runModeLabel, which merges repeated family/mode values. |
| 38. Secret bundle Attach and key Delete can fire twice | Fixed | Bundle actions keep independent pending keys; Attach and key-delete controls disable their operation. Real Apollo regressions prove duplicate calls reach the transport once and failures release admission. |
| 39. Add / update ref button stays clickable during save | Fixed | Reference mutation variables and pending keys use the trimmed environment variable. The view disables that ref save and keeps drafts changed during an earlier request. |
| 40. Agent secrets dialog leaks reveal timers on close | Fixed | Closing, changing the environment spec or unmounting cancels timers and invalidates late reveal responses; a cleared older timer cannot hide a later reveal. Real Apollo/timer regressions cover each boundary. |
| 41. Agent secrets page assumes env spec slug equals agent slug | Remaining | Current SecretsContent still passes agent slug directly as envSpecSlug and documents that assumption; an actual association read is needed. |
| 42. Register tool form has two duplicate close buttons | Pre-existing | AddToolForm dialog was replaced by AddToolScreen/use-add-tool page flow; duplicate dialog cancellation path is absent. |
| 43. PageShell imports from app/, breaking the components rule | Remaining | PageShell still imports useAppChrome from an app route directory. |
| 44. Profile identity copy points to the old organization settings path | Pre-existing | Earlier profile organization-link correction is included in the audited base. |
| 45. AppearanceClient export actually renders language and timezone | Pre-existing | ProfilePreferences now contains language/timezone only; appearance has its own settings page. |
| 46. 'Cannot delete the active provider' toast is unreachable | Remaining | Active IdP deletion is disabled in the view; unreachable defensive hook toast remains cleanup work. |
| 47. Identity provider client ID always gets an ellipsis | Pre-existing | Earlier IdP client-id truncation correction is included in the audited base. |
| 48. 'Active since' falls back to updatedAt for legacy providers | Pre-existing | Earlier IdP observed activation-date correction is included in the audited base. |
| 49. Agents Active and History tables show raw ISO timestamps | Pre-existing | Current AgentRunScreen Time formats startedAt/finishedAt with formatRelativeAge; old Active/History components are gone. |
| 50. useAgentBoxes hides includeEnded behind a type cast | Pre-existing | useAgentBoxes uses AgentBoxesVars directly with orgId/includeEnded; hidden variable cast is gone. |
| 51. 'No other sessions' toast can never fire | Remaining | No-other-session toast remains a defensive hook path behind a disabled sign-out-all view. |
| 52. Identity providers doc link label does not match its target | Pre-existing | Earlier identity-provider organization-label documentation correction is included in the audited base. |
| 53. Introduction, Get Started and Changelog describe an unrelated boilerplate | Remaining | Introduction/GetStarted/Changelog still require product-fact review; this branch does not claim their static content is accurate. |
| 54. Changelog shows unknown entry types as destructive | Remaining | ChangelogScreen.typeVariant still gives unrecognized types destructive styling. |
| 55. Cluster prerequisites renders 'whosespec.ingressClassName' | Pre-existing | Earlier cluster prerequisite spec spacing correction is included in the audited base. |
| 56. Cluster prerequisites cert-manager text loses a space | Pre-existing | Earlier prerequisite cert-manager spacing correction is included in the audited base. |
| 57. Configuration docs handle an 'image' source nothing uses | Remaining | ConfigurationScreen still advertises image source; only story fixtures currently supply image-source rows. |
| 58. Help copyDiagnostics timer is never cleared | Fixed | Help copy timers are replaced on repeat and cleared at unmount; clipboard completion after unmount is ignored. |
| 59. Mute hours above 168 are not rejected in the submit handler | Pre-existing | display-logic.test.tsx verifies integer 1..168-hour boundaries and pending refusal. |
| 60. Dashboard loading skeleton no longer matches the layout | Pre-existing | DashboardLoading was replaced by components/home/HomeScreen HomeSkeleton over current panel spans. |
| 61. Dashboard counts recent failures twice | Pre-existing | Old DashboardScreen failure tiles are gone; current HomeScreen/HomeScreen.test.tsx use permission-scoped home panels. |
| 62. ApprovalScreen computes unused approve and reject CTA flags | Remaining | ApprovalScreen still computes unused showApproveCta/showRejectCta. |
| 63. Approval history tones use hard-coded rgb() values | Remaining | ApprovalHistory still returns inline rgb colors for tone borders/icons. |
| 64. Approvals select-all is wrong when stale ids remain selected | Pre-existing | display-logic.test.tsx verifies visible approval selection after polling, including stale ids. |
| 65. Form submissions sparkline mixes local and UTC dates | Parked | Forms route flag remains false. Do not enable or claim runtime fulfillment of sparkline time display. |
| 66. Form detail says 'No published form found' for any missing form | Pre-existing | Earlier missing-form copy fix is included; Forms remains parked independently of this copy correction. |
| 67. Managed domains table flashes the skeleton on poll | Pre-existing | ManagedDomainsScreen now delegates loading/rows to shared ListPage/DataTable rather than replacing its whole table on poll. |
| 68. Downloads PackageRow has unused badge and disabled props | Remaining | DownloadsScreen PackageRow still declares unused badge/disabled props. |
| 69. Redeploy input shape differs between detail and list screens | Pre-existing | use-deployment-detail.onRedeploy now sends input {id: deployment.id}, matching other actual callers. |
| 70. Metrics footer links Temporal UI to localhost | Fixed | Metrics receives existing NEXT_PUBLIC_TEMPORAL_UI_URL from its route container; hides link when unconfigured; focused React check. |
| 71. Ops cluster status comment contradicts the code | Pre-existing | use-ops now reads scoped queries; obsolete disabled-cluster aggregation comment is absent there. |
| 72. Job run breadcrumbs link to ?tab= values the Jobs page ignores | Pre-existing | Jobs use actual runs/commands routes; old ignored ?tab= links are absent. |
| 73. Command run page comment says no singular query exists | Pre-existing | Old commands page is a documented redirect to Logs Commands; stale singular-query comment is absent. |
| 74. Task output storage copy contradicts itself | Pre-existing | Old TasksScreen inline-output placeholder is absent; TaskRunDetail consistently documents pod output/console link. |
| 75. Playground hook order depends on a route flag | Parked | Playground route flag remains false; no feature activation or hook-order acceptance work in this branch. |
| 76. Playground runs entirely on simulated responses | Parked | Playground remains parked; no simulated inference is wired or advertised as successful. |
| 77. Invitation expiry badge shows NaN for unparseable dates | Pre-existing | display-logic.test.tsx verifies invalid/nonfinite invitation expiry remains unavailable. |
| 78. 'New Pipeline' links to a route that does not exist | Pre-existing | pipelines/new has actual page/new-pipeline-client routes; it no longer falls through to a detail id of new. |
| 79. Pipeline secrets route opens on the Runs tab | Pre-existing | usePipelineDetail selects secrets when the actual pathname ends with /secrets; this branch already honors the route independently of a tab query. |
| 80. Pipeline detail title is always 'Pipeline' | Remaining | PipelineDetailClient still reads usePipelineDetail runs; no pipeline-name lookup is wired on this audited branch. |
| 81. Webhook row actions disable the button on every row | Fixed | Real concurrent Apollo request regression verifies synchronous same-row exclusion and independent release on transport/success; row menu uses pendingRows. |
| 82. Create sheets default slugs the backend rejects | Fixed | Shared default slug generation obeys core/naming.py letter-start/40-char/end-alnum rules; create sheets/hooks refuse invalid submissions. |
| 83. Create project ignores ?team= | Fixed | useProjects passes team query through dialog props; requested visible team is selected after load/reopen, unavailable request does not silently select another. |
| 84. New workflow definition lookup is not org-scoped | Fixed | useConfigureWorkflow waits for active org and passes orgId to real definition reads; Apollo regression checks org switches. |
| 85. Workflow builder shows 'no create access' while entitlement loads | Fixed | Workflow builder distinguishes entitlementLoading from denial; disabled submit also blocks form-submit bypass until access is known. |
| 86. 'Needs attention' tile links to a missing #workflows anchor | Fixed | Agent-only Needs attention links to project-filtered agent route; workflow projects retain their real workflows anchor. |
| 87. Token expiry field rejects 0 despite help text | Pre-existing | Earlier token creation zero-day/no-expiry correction and regression are included in the audited base. |
| 88. Workflow pillar page and shell both subscribe to useTieredWorkflow | Pre-existing | Workflow frame supplies useFramedWorkflow context; builder-content consumes it rather than adding the old shell/page query pair. |
| 89. Templates tab renders rows while loading and points empty state at builder | Pre-existing | Separate WorkflowTemplatesScreen was replaced by unified WorkflowsListScreen; Templates view shares ListPage loading and /workflows/new create links. |
| 90. Duplicate SVG gradient ids across clusters on the metrics page | Fixed | Real Chromium SVG inspection verifies 12 distinct gradients and chart-local references across two cluster panels. |

## Dependencies and limits

- Rotation grace (#21) needs a truthful configured value before confirmation; do not replace it with another guessed default. Agent environment-spec association (#41) needs the actual persisted relationship rather than a slug convention.
- Copies of the wizard, the app Chrome context location, unused props/flags, and presentation-token cleanup remain listed explicitly; this bounded fix does not redesign those surfaces.
- The only locale edit changes the existing identical English manifest-conflict description in all eight catalogs to describe an unsaved draft. Full translation work remains #2145.
