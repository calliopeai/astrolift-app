"""Owned stateful AWS HTTP fixture; emits no credentials or request bodies."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

import boto3

ACCOUNT = "123456789012"
REGION = "us-west-2"
ISSUER = "oidc.eks.us-west-2.amazonaws.com/id/FIXTURE"


class AwsSocket:
    def __init__(self, cluster, kube):
        self.cluster, self.kube = cluster, kube
        self.calls = []
        self.role = None
        self.group = None
        self.policy = None
        self.reader_denied = False
        self.after_filter = None
        self.foreign_endpoint = False
        self.fail_action = None
        self.before = None
        self.role_id = "ROLE-OWNED-ONE"
        self.group_time = 1767225600000
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, payload, status=200, xml=False):
                raw = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/xml" if xml else "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def xml(self, action, body):
                self.send(
                    f"<{action}Response><{action}Result>{body}</{action}Result><ResponseMetadata><RequestId>owned-fixture</RequestId></ResponseMetadata></{action}Response>",
                    xml=True,
                )

            def err(self, code, status=403):
                self.send(
                    f"<ErrorResponse><Error><Code>{code}</Code><Message>PRIVATE_PROVIDER_MARKER</Message></Error></ErrorResponse>",
                    status,
                    True,
                )

            def do_GET(self):
                owner.calls.append("DescribeCluster")
                self.send(
                    {
                        "cluster": {
                            "arn": f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/disposable-eks",
                            "status": "ACTIVE",
                            "endpoint": "https://foreign.invalid"
                            if owner.foreign_endpoint
                            else owner.cluster.endpoint,
                            "certificateAuthority": {"data": owner.cluster.ca_cert},
                            "identity": {"oidc": {"issuer": "https://" + ISSUER}},
                        }
                    }
                )

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                target = self.headers.get("X-Amz-Target", "")
                if target:
                    action = target.rsplit(".", 1)[1]
                    params = json.loads(raw)
                else:
                    params = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    action = params.pop("Action")
                    params.pop("Version", None)
                owner.calls.append(action)
                if owner.before:
                    fn, owner.before = owner.before, None
                    fn(action)
                if owner.fail_action == action:
                    return self.err("ServiceUnavailable", 503)
                if action == "AssumeRole":
                    owner.assumption = {"role": params["RoleArn"], "external_id": params.get("ExternalId")}
                    return self.xml(
                        action,
                        "<Credentials><AccessKeyId>ASIACOLLECTORFIXTURE</AccessKeyId><SecretAccessKey>local-fixture-only</SecretAccessKey><SessionToken>local-fixture-only</SessionToken><Expiration>2099-01-01T00:00:00Z</Expiration></Credentials>",
                    )
                if action == "GetCallerIdentity":
                    return self.xml(
                        action,
                        f"<Account>{ACCOUNT}</Account><Arn>arn:aws:sts::{ACCOUNT}:assumed-role/registered/fixture</Arn><UserId>owned</UserId>",
                    )
                if action == "GetOpenIDConnectProvider":
                    return self.xml(
                        action,
                        f"<Url>{ISSUER}</Url><ClientIDList><member>sts.amazonaws.com</member></ClientIDList>",
                    )
                if action == "GetRole" and not owner.role:
                    return self.err("NoSuchEntity", 404)
                if action in {"GetRole", "CreateRole"}:
                    if action == "CreateRole":
                        owner.role = params
                    name = owner.role["RoleName"]
                    tags = "".join(
                        f"<member><Key>{escape(v)}</Key><Value>{escape(owner.role[k.replace('.Key', '.Value')])}</Value></member>"
                        for k, v in owner.role.items()
                        if k.startswith("Tags.member.") and k.endswith(".Key")
                    )
                    trust = owner.role["AssumeRolePolicyDocument"]
                    return self.xml(
                        action,
                        f"<Role><Path>/astrolift/</Path><RoleName>{name}</RoleName><RoleId>{owner.role_id}</RoleId><Arn>arn:aws:iam::{ACCOUNT}:role/astrolift/{name}</Arn><CreateDate>2026-01-01T00:00:00Z</CreateDate><Tags>{tags}</Tags><AssumeRolePolicyDocument>{escape(quote(trust, safe=''))}</AssumeRolePolicyDocument></Role>",
                    )
                if action == "ListRolePolicies":
                    member = f"<member>{owner.policy['PolicyName']}</member>" if owner.policy else ""
                    return self.xml(
                        action, f"<PolicyNames>{member}</PolicyNames><IsTruncated>false</IsTruncated>"
                    )
                if action == "ListAttachedRolePolicies":
                    return self.xml(action, "<AttachedPolicies/><IsTruncated>false</IsTruncated>")
                if action == "GetRolePolicy":
                    return self.xml(
                        action,
                        f"<PolicyDocument>{escape(quote(owner.policy['PolicyDocument'], safe=''))}</PolicyDocument><PolicyName>{owner.policy['PolicyName']}</PolicyName><RoleName>{owner.role['RoleName']}</RoleName>",
                    )
                if action == "PutRolePolicy":
                    owner.policy = params
                    return self.xml(action, "")
                if action == "UpdateAssumeRolePolicy":
                    owner.role["AssumeRolePolicyDocument"] = params["PolicyDocument"]
                    return self.xml(action, "")
                if action == "DescribeLogGroups":
                    groups = (
                        []
                        if not owner.group
                        else [
                            {
                                "logGroupName": owner.group["logGroupName"],
                                "arn": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{owner.group['logGroupName']}:*",
                                "creationTime": owner.group_time,
                                **(
                                    {"retentionInDays": owner.group["retentionInDays"]}
                                    if "retentionInDays" in owner.group
                                    else {}
                                ),
                            }
                        ]
                    )
                    return self.send({"logGroups": groups})
                if action == "CreateLogGroup":
                    owner.group = params
                    return self.send({})
                if action == "PutRetentionPolicy":
                    owner.group["retentionInDays"] = params["retentionInDays"]
                    return self.send({})
                if action == "ListTagsForResource":
                    return self.send({"tags": owner.group["tags"]})
                if action == "FilterLogEvents":
                    if owner.reader_denied:
                        return self.send(
                            {"__type": "AccessDeniedException", "message": "PRIVATE_READER_MARKER"}, 400
                        )
                    request = Request(
                        owner.cluster.endpoint,
                        data=raw,
                        headers={
                            "Content-Type": "application/json",
                            "X-Amz-Target": "Logs_20140328.FilterLogEvents",
                        },
                    )
                    with urlopen(request, timeout=5) as response:
                        payload = json.loads(response.read())
                    if owner.after_filter:
                        owner.after_filter()
                    return self.send(payload)
                self.err("NotImplemented", 500)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.session = self.new_session()

    def new_session(self):
        session = boto3.Session(
            region_name=REGION,
            aws_access_key_id="ambient-owned-fixture",
            aws_secret_access_key="local-fixture-only",
        )
        original = session.client

        def client(name, **kwargs):
            kwargs["endpoint_url"] = self.url
            return original(name, **kwargs)

        session.client = client
        return session

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
