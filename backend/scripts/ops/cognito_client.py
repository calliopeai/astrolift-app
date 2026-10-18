"""Inspect and clean up a Cognito app client from an operator shell (#2055, #1727).

boto3 only, no Django. Run it with your own AWS SSO profile::

    python backend/scripts/ops/cognito_client.py --profile <profile> show --pool <pool> --client <id>
    python backend/scripts/ops/cognito_client.py --profile <profile> trim --pool <pool> --client <id> \\
        --keep-callback <url> ... --keep-logout <url> ... [--apply]
    python backend/scripts/ops/cognito_client.py --profile <profile> clients --pool <pool>
    python backend/scripts/ops/cognito_client.py --profile <profile> retire --pool <pool> --client <id> [--apply]

``UpdateUserPoolClient`` resets every field the request leaves out, so
``trim`` sends back every writable field ``DescribeUserPoolClient``
returned, with only CallbackURLs and LogoutURLs trimmed. The writable set
comes from botocore's UpdateUserPoolClient input shape. If Describe returns
a field the tool cannot send back (a Cognito field newer than the installed
botocore), it refuses instead of resetting it. After ``--apply`` it reads the
client again and fails unless only the two lists changed.

Without ``--apply`` nothing is written. The runbook is
docs/operators/cognito-client-cleanup.md.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3

# Describe returns these, and Update has no parameter for them.
READ_ONLY = frozenset({"ClientSecret", "CreationDate", "LastModifiedDate"})
LISTS = ("CallbackURLs", "LogoutURLs")
BRANDING_QUOTA = 20
_RAW = "_cognito_client_raw_describe_keys"


class Refusal(Exception):
    """The tool will not act, and nothing was written."""


def cognito(region: str, profile: str | None = None):
    client = boto3.Session(profile_name=profile, region_name=region).client("cognito-idp")
    watch_raw_describe(client)
    return client


def watch_raw_describe(client) -> None:
    """Record the keys of each raw DescribeUserPoolClient body.

    botocore drops response keys its model does not know, so a field newer
    than the installed botocore would be invisible in the parsed result and
    silently reset by the update. The raw body still carries it.
    """
    seen: list[set[str]] = []

    def capture(response_dict, **_):
        if response_dict.get("status_code") == 200 and response_dict.get("body"):
            seen.append(set(json.loads(response_dict["body"]).get("UserPoolClient", {})))

    client.meta.events.register("before-parse.*.DescribeUserPoolClient", capture)
    setattr(client, _RAW, seen)


def writable_fields(client) -> frozenset[str]:
    shape = client.meta.service_model.operation_model("UpdateUserPoolClient").input_shape
    return frozenset(shape.members)


def describe(client, pool: str, client_id: str) -> dict:
    """The client's config; refuses when it holds a field Update cannot send back."""
    seen = getattr(client, _RAW, [])
    seen.clear()
    config = client.describe_user_pool_client(UserPoolId=pool, ClientId=client_id)["UserPoolClient"]
    keys = set(config).union(*seen)
    unknown = sorted(keys - writable_fields(client) - READ_ONLY)
    if unknown:
        raise Refusal(
            f"DescribeUserPoolClient returned {', '.join(unknown)}, which this botocore cannot send "
            "back through UpdateUserPoolClient, so an update would reset it. Upgrade boto3/botocore "
            "and run again."
        )
    return config


def plan_trim(config: dict, keep_callbacks: list[str], keep_logouts: list[str]) -> dict:
    """The two trimmed lists. Trim only removes; a kept URL must already be on the client."""
    new = {}
    for name, keep in (("CallbackURLs", keep_callbacks), ("LogoutURLs", keep_logouts)):
        current = config.get(name, [])
        missing = [url for url in keep if url not in current]
        if missing:
            raise Refusal(f"{name} does not contain {', '.join(missing)}; trim only removes URLs.")
        new[name] = [url for url in current if url in keep]
    default = config.get("DefaultRedirectURI")
    if default and default not in new["CallbackURLs"]:
        raise Refusal(f"DefaultRedirectURI {default} would no longer be a callback URL; keep it.")
    return new


def update_request(client, config: dict, new: dict) -> dict:
    """Every writable field Describe returned, with the two lists replaced."""
    writable = writable_fields(client)
    return {key: value for key, value in config.items() if key in writable} | new


def drift(before: dict, after: dict, new: dict) -> list[str]:
    """Fields that changed other than the two lists, plus a list that did not land."""
    ignore = {"LastModifiedDate", *LISTS}
    changed = sorted(k for k in (set(before) | set(after)) - ignore if before.get(k) != after.get(k))
    changed += [name for name in LISTS if sorted(after.get(name, [])) != sorted(new[name])]
    return changed


def diff_lines(config: dict, new: dict) -> list[str]:
    lines = []
    for name in LISTS:
        current = config.get(name, [])
        lines.append(f"{name}: {len(current)} -> {len(new[name])}")
        lines += [f"  {'  ' if url in new[name] else '- '}{url}" for url in current]
    return lines


def trim(client, pool, client_id, keep_callbacks, keep_logouts, apply, out=print) -> int:
    before = describe(client, pool, client_id)
    new = plan_trim(before, keep_callbacks, keep_logouts)
    for line in diff_lines(before, new):
        out(line)
    if all(sorted(before.get(name, [])) == sorted(new[name]) for name in LISTS):
        out("Nothing to trim.")
        return 0
    if not apply:
        out("Dry run: nothing written. Add --apply to update the client.")
        return 0
    client.update_user_pool_client(**update_request(client, before, new))
    changed = drift(before, describe(client, pool, client_id), new)
    if changed:
        out(f"FAILED: after the update these fields differ from the plan: {', '.join(changed)}")
        return 1
    out("Updated: only CallbackURLs and LogoutURLs changed.")
    return 0


def show(client, pool, client_id, out=print) -> int:
    config = describe(client, pool, client_id)
    if "ClientSecret" in config:
        config = config | {"ClientSecret": "[redacted]"}
    out(json.dumps(config, indent=2, sort_keys=True, default=str))
    return 0


def has_branding(client, pool, client_id) -> bool:
    try:
        client.describe_managed_login_branding_by_client(UserPoolId=pool, ClientId=client_id)
    except client.exceptions.ResourceNotFoundException:
        return False
    return True


def clients(client, pool, out=print) -> int:
    rows = []
    for page in client.get_paginator("list_user_pool_clients").paginate(UserPoolId=pool, MaxResults=60):
        for item in page["UserPoolClients"]:
            config = client.describe_user_pool_client(UserPoolId=pool, ClientId=item["ClientId"])
            config = config["UserPoolClient"]
            rows.append(
                (
                    item["ClientId"],
                    item.get("ClientName", ""),
                    len(config.get("CallbackURLs", [])),
                    len(config.get("LogoutURLs", [])),
                    has_branding(client, pool, item["ClientId"]),
                )
            )
    out(f"{'client id':<28} {'callbacks':>9} {'logouts':>7}  branding  name")
    for client_id, name, callbacks, logouts, branded in rows:
        out(f"{client_id:<28} {callbacks:>9} {logouts:>7}  {'yes' if branded else 'NO':<8}  {name}")
    branded = sum(1 for row in rows if row[4])
    out(f"{len(rows)} clients; {branded} of {BRANDING_QUOTA} managed login brandings in use.")
    return 0


def retire(client, pool, client_id, apply, out=print) -> int:
    config = describe(client, pool, client_id)
    callbacks = config.get("CallbackURLs", [])
    if callbacks:
        raise Refusal(
            f"{config.get('ClientName', client_id)} still has {len(callbacks)} callback URL(s); "
            "trim them first."
        )
    if not apply:
        out(f"Dry run: would delete {config.get('ClientName', '')} ({client_id}). Add --apply.")
        return 0
    client.delete_user_pool_client(UserPoolId=pool, ClientId=client_id)
    out(f"Deleted {config.get('ClientName', '')} ({client_id}).")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--profile", help="AWS profile (default: the usual boto3 chain)")
    p.add_argument("--region", help="default: the region prefix of --pool")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("show", "trim", "clients", "retire"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--pool", required=True)
        if name != "clients":
            cmd.add_argument("--client", required=True)
        if name in ("trim", "retire"):
            cmd.add_argument("--apply", action="store_true")
        if name == "trim":
            cmd.add_argument("--keep-callback", nargs="*", required=True, default=[])
            cmd.add_argument("--keep-logout", nargs="*", required=True, default=[])
    return p


def main(argv: list[str] | None = None, client=None) -> int:
    args = parser().parse_args(argv)
    client = client or cognito(args.region or args.pool.split("_", 1)[0], args.profile)
    try:
        if args.command == "show":
            return show(client, args.pool, args.client)
        if args.command == "clients":
            return clients(client, args.pool)
        if args.command == "retire":
            return retire(client, args.pool, args.client, args.apply)
        return trim(client, args.pool, args.client, args.keep_callback, args.keep_logout, args.apply)
    except Refusal as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
