# Shared settings localization (#2145)

`SettingsPage`, `SettingsSection`, `Restricted`, and `PermissionNote` use
`shared.settings` and `shared.restricted` in English, Spanish, French, German,
Brazilian Portuguese, Japanese, Korean, and Simplified Chinese. Settings navigation
and select labels, danger-zone copy, default Save/Cancel/pending actions, and
permission notices follow the active locale. The permission ID remains literal in
a rich `<code>` slot; the translated sentence may place it according to its grammar.

Caller-owned section titles, descriptions, action labels, custom `saveLabel` and
`verb` values, field values, and server diagnostics remain as supplied. Screen
owners must translate their own custom labels. This slice adds only the two shared
catalog objects and does not change other messages or any screen namespace.

Read-only show/hide preferences, disabled fields and destructive controls, dirty
and pending save checks, submit/cancel callbacks, section IDs and URL behavior
retain their existing contracts. Switching locale does not remount the editor or
clear a draft. The component has no keyboard-save handler or shortcut tooltip;
this change adds neither a shortcut nor a claim that one exists.

Validation covers all eight locales with real `NextIntlClientProvider` rendering,
ICU argument/rich-tag contracts, unchanged permission IDs and caller overrides,
read-only submit refusal, hidden fields, pending-submit refusal, raw errors and
retry callbacks, and draft retention across pending state and locale changes.
Localized French, Spanish, and Japanese stories exercise the shared defaults.

The focused affected run passes 165 tests across the new 34 localization cases,
existing settings section navigation, both complete settings story groups,
app identity/members/deregister, cluster ingress-auth, and form state, accessibility,
mutation feedback, and state-hook regressions. TypeScript, ESLint and formatting
pass. This is the shared settings slice of #2145; broader catalog completion is
tracked by the coordinated screen owners.
