# Typed brain read tokens, one brain at a time

Brain read tokens can carry typed read principals: contract `"1"`, agreed in
calliopeai/astrolift-app#2470 and ConflictHQ/mimwell-pvt#527. A brain gets them
only after the platform operator stages it and then enables issuance for it.
Every other brain keeps the four legacy token shapes, byte for byte. Nothing
here advertises a shared contract version.

Preconditions: a shell in the control plane's backend container for `manage.py brain_typed_tokens`; `--operator` is the email of an active superuser; the brain and every app hosted on it need a deploy (`app.deploy` on each) to pick up the settings; the replica checks need a kubeconfig for the cluster serving the brain; `astro brain connect` needs read access to the brain. Placeholders: `<brain app guid>` is `id` from `astroliftBrains` (a brain is a RegisteredApp); `<brain-slug>` is its `slug`; `<email>` is the operator's email; `<what you checked on every serving replica>` is free text, 20 to 500 characters.

## What a typed token adds

A typed token keeps every legacy claim and adds these:

| Family | `sub_kind` | `teams` | Extra claim |
|---|---|---|---|
| A person (`issueBrainReadToken`, `astrolift_connect_brain`) | `user` | Their live teams in the brain's organization, capped to the calling API token's team | none |
| A run a person launched | `user` | The same, capped to the team of the API token that launched the run | none |
| A run nobody launched, reading through the agent's share | `agent` | `[]` | `agent`: the Workload GUID |
| An app hosted on the brain | `app` | `[]` | `app`: the RegisteredApp GUID |

`org` is the brain's organization GUID. Every identifier is a GUID; slugs never
grant anything. A team counts only when the person's membership, the team and
their membership of the organization are all live. A typed token grants reads
only.

This install refuses a typed token rather than trim it. The limits are local
host limits, not shared contract guarantees:

- more than 64 teams: refused, naming the limit;
- an encoded token over 8192 bytes: the same.

A person gets `PRECONDITION` from `issueBrainReadToken` and `unavailable` from
`astrolift_connect_brain`; a run's spawn or refresh fails; a hosted app's
deploy fails.

## The brain's verifier settings

Every deploy of a brain sets `BRAIN_MCP_JWKS_URL`, `BRAIN_MCP_ISSUER` and
`BRAIN_MCP_AUDIENCE` (#2441). A staged brain's deploy also sets:

- `BRAIN_MCP_TYPED_PRINCIPALS=true`
- `BRAIN_MCP_EXPECTED_ORG_GUID=<the brain's organization GUID>`

The install sets them in each container's own `env`, which Kubernetes ranks
above the `envFrom` Secrets, so on a staged brain neither the manifest nor a
secret bundle can change them. On any other brain the install removes both
from the containers' `env`. A value from the manifest's `[env]` or a secret
bundle still reaches the pod through `envFrom`, but no typed token is issued
for an unstaged brain, so it cannot change what the brain admits.

## The command

Run it in the control plane's container. Without `--apply` a step runs its
checks and changes nothing. `--operator` must be the email of an active
superuser; every applied step is audited as them (`brain.typed_tokens.*`).

```sh
python manage.py brain_typed_tokens status
python manage.py brain_typed_tokens stage   <brain app guid> --operator <email> [--apply]
python manage.py brain_typed_tokens enable  <brain app guid> --operator <email> \
    --attestation "<what you checked on every serving replica>" [--apply]
python manage.py brain_typed_tokens disable <brain app guid> --operator <email> [--apply]
python manage.py brain_typed_tokens unstage <brain app guid> --operator <email> [--apply]
```

## Turning it on

1. Deploy a brain image whose MCP verifier reads typed principals
   (ConflictHQ/mimwell-pvt#523). An older engine ignores the typed claims and
   reads the person by email only.
2. `stage --apply`. Tokens stay legacy.
3. Deploy every environment that serves the brain, so each one renders the two
   settings above.
4. On every serving replica, check the image and that both settings are in its
   environment, and that a legacy token still reads (`astro brain connect`, then
   an MCP `initialize` through the edge).

   ```sh
   astro brain connect <brain-slug>
   ```

5. `enable --apply` with an attestation of what you checked, 20 to 500
   characters. It is refused unless the brain is staged and every environment
   that serves it runs only deployments made after staging.
6. Read the brain once with a fresh token from step 4's command. If a replica
   refuses it, `disable --apply` at once: new tokens are legacy from then on.

## Turning it off

1. `disable --apply`. New tokens are legacy at once.
2. Typed tokens already issued to people and runs verify for up to 15 minutes.
   Each app hosted on the brain keeps its typed token until it is redeployed:
   deploy each environment of each hosted app twice, since a deploy keeps the
   key of the release it replaces published.
3. `unstage --apply`. It is refused while issuance is on, for 16 minutes after
   it stopped, and while a hosted app's published key still signed a typed
   token, naming the apps.
4. Deploy the brain. Its workloads lose the two typed settings.

Unstaging and redeploying the brain before typed tokens expire would make the
brain refuse them, so the command holds the order.

## Verify

```sh
python manage.py brain_typed_tokens status
astro brain connect <brain-slug>
```

`status` prints `<org slug>/<brain slug> <guid>: staged ...; issuing typed tokens since ...` for the brain (`No brain is staged for typed read tokens.` after unstaging), and the token `connect` prints reads the brain through its MCP endpoint.
