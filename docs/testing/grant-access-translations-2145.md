# Grant-access translations (#2145)

This leaf finishes the parked grant-access draft from `4551e4cf`. Its isolated
worktree is `/tmp/astrolift-grant-access-translations-2145`, branch
`fix/grant-access-translations-2145`. It does not import other branch history or
alter shared-model source, backend authorization, SDL, generated client code or
portal data.

## Scope

`shared.access.grant` contains 75 message leaves in each supported locale:
English, Spanish, French, German, Brazilian Portuguese, Japanese, Korean and
Simplified Chinese. The grant page translates its title and breadcrumb switches;
the flow translates Who, Role, Scope, Expiry and Review, validation, search/read
failures, exact preview counts, group membership descriptions, outcomes and retry
actions. Scope/permission/source presentation uses the existing localized access
helpers rather than altering grant rules.

Role names, principal names and IDs, group external IDs, permission slugs, unknown
scope kinds, server refusal messages and server notes stay literal. English
defaults of the existing exported presentation helpers remain available to
legacy callers. No obsolete keys were removed and no English catalog fallback
was copied into the seven translated catalogs.

The date review formats the chosen civil date in the locale with an explicit UTC
display anchor. `expiryToIso` still sends the existing local-day-end expiry;
changing locale or the formatter timezone does not change the chosen draft date.

The inherited draft's feedback safeguards are retained: a failed/missing preview
disables granting, an empty result stays on review instead of invoking success,
and a successful write followed by a failed list refresh returns the successful
outcome with a localized warning. Partial retries send only failed recipients.
An explicitly refused preview with an absent or empty reason gets localized
refusal copy and remains ungrantable; a nonempty server reason is preserved.

## Verification

From `frontend/`, these commands passed:

```sh
npx vitest run components/access/grant-preview.test.ts components/access/grant-access-translations.test.tsx components/access/use-grant-access.test.tsx components/screens/administration/access/use-grant-page.test.tsx --maxWorkers=2
npx vitest run components/stories.test.tsx -t GrantAccessFlow --maxWorkers=1
npx tsc --noEmit
```

- 86 focused checks: all eight locales render actual validation/review/date/retry
  interactions, retain exact authoritative counts beyond returned rows, preserve
  raw refusals, and retain the edited draft across a Spanish-to-Japanese locale
  change. Every message's ICU arguments and rich tags match across locales,
  including every plural branch. French SSR/hydration remains stable with
  different server/browser formatter timezones.
- Six of those checks use the real Apollo `HttpLink` and actual operation
  documents against an HTTP fetch fixture executed and validated with the
  tracked full SDL. They verify search variables/external IDs, preview refusal
  and fallback, per-recipient write failure, exact successful write inputs,
  refresh failure without a second write, and no write for an unsupported token
  holder. These are controlled transport tests, not live tenant writes or
  backend permission proofs.
- 25 portable GrantAccessFlow stories pass, including the genuine French
  768-pixel refusal frame and keyboard retry. Other portable stories are filtered
  out. This is DOM interaction evidence; no browser layout/build claim is made.
- ESLint passes for the eight affected source/test files. Prettier passes for
  those files and all eight changed catalogs. `git diff --check` passes.

Validation logs are `/tmp/astrolift-grant-access-focused-final.log`,
`/tmp/astrolift-grant-access-portable.log`,
`/tmp/astrolift-grant-access-tsc.log`, `/tmp/astrolift-grant-access-lint.log` and
`/tmp/astrolift-grant-access-format-check.log`. Existing dependencies are linked
from `/tmp/astrolift-next-web-batch/frontend/node_modules`; no dependency versions
or lockfiles were changed.

## Boundaries

This completes the grant-owned copy slice, not the whole translation issue.
The reusable `ShellHeader` still owns its generic English breadcrumb landmark
and switcher aria suffix in this base; that shared chrome is outside this leaf.
No full frontend suite, production build, browser layout run, deployment or
native/mobile gate was run for this bounded translation change.
