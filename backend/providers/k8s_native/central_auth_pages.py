"""Astrolift-branded pages for the central auth host (#2285).

oauth2-proxy renders two pages a person can land on: ``sign_in.html`` (only
when it cannot skip straight to the IdP) and ``error.html`` (a refused or
failed sign-in). Both are Go templates read from ``--custom-templates-dir``;
a file that is missing falls back to oauth2-proxy's own page, so these only
ever replace, never remove.

The fields used are the ones oauth2-proxy 7.6 passes (the chart pinned in
``central_auth``): sign-in gets ProxyPrefix, Redirect, ProviderName and
SignInMessage; error gets Title, Message, StatusCode, ProxyPrefix, Redirect
and RequestID. Same tokens and mark as the app and the CLI device pages; no
external requests.
"""

from __future__ import annotations

TEMPLATES_CONFIGMAP = "astrolift-central-auth-templates"
TEMPLATES_DIR = "/etc/astrolift/auth-templates"

_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{title} · Astrolift</title>
<style>
:root{{--ground:#000;--panel:#0a0a0a;--brand:#04231a;--fg:#f0f2f1;--muted:#848b88;
--line:rgba(255,255,255,.1);--action:#2f9e52;--ink:#04241b;--err:#ff6b6b;
--sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;background:var(--ground);color:var(--fg);font:15px/1.5 var(--sans);
display:flex;flex-direction:column}}
.brand{{display:flex;align-items:center;gap:.6rem;padding:.9rem 1.25rem;background:var(--brand);
border-bottom:1px solid var(--line)}}
.brand span{{font-weight:600;font-size:.95rem}}
main{{flex:1;display:flex;align-items:flex-start;justify-content:center;padding:3rem 1rem}}
.card{{width:100%;max-width:28rem;background:var(--panel);border:1px solid var(--line);border-radius:2px;
padding:1.75rem}}
h1{{font-size:1.2rem;margin:0 0 .5rem}}
p{{color:var(--muted);margin:0 0 1rem}}
.code{{font-family:var(--mono);font-size:.8rem;color:var(--muted)}}
.err{{color:var(--err)}}
a.button,button{{display:block;width:100%;text-align:center;padding:.7rem 1rem;border-radius:2px;
font:600 .875rem var(--sans);cursor:pointer;background:var(--action);color:var(--ink);
border:1px solid var(--action);text-decoration:none}}
a.button:focus-visible,button:focus-visible{{outline:none;box-shadow:0 0 0 3px rgba(47,158,82,.45)}}
@media (max-width:420px){{main{{padding:1.5rem .75rem}}.card{{padding:1.25rem}}}}
</style>
</head>
<body>
<header class="brand">
<svg width="22" height="22" viewBox="0 0 32 32" fill="none" aria-hidden="true">
<path d="M16 3 L26 27 L16 22 L6 27 Z" stroke="#2F9E52" stroke-width="2" stroke-linejoin="round"/>
<circle cx="16" cy="14" r="2.5" fill="#2F9E52"/></svg>
<span>Astrolift</span>
</header>
<main><div class="card">
"""

_FOOT = """</div></main>
</body>
</html>
"""

SIGN_IN_HTML = (
    _HEAD.format(title="Sign in")
    + """<h1>Sign in to Astrolift</h1>
{{ if .SignInMessage }}<p>{{ .SignInMessage }}</p>{{ end }}
<form method="GET" action="{{ .ProxyPrefix }}/start">
<input type="hidden" name="rd" value="{{ .Redirect }}">
<button type="submit">Continue with {{ .ProviderName }}</button>
</form>
"""
    + _FOOT
)

ERROR_HTML = (
    _HEAD.format(title="Sign-in problem")
    + """<h1>{{ .Title }}</h1>
<p>{{ .Message }}</p>
<p class="code"><span class="err">{{ .StatusCode }}</span>{{ if .RequestID }} · request {{ .RequestID }}{{ end }}</p>
{{ if .Redirect }}<a class="button" href="{{ .ProxyPrefix }}/sign_in?rd={{ .Redirect }}">Sign in again</a>{{ end }}
"""
    + _FOOT
)


def templates_configmap() -> dict:
    """The ConfigMap the oauth2-proxy release mounts at ``TEMPLATES_DIR``."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": TEMPLATES_CONFIGMAP},
        "data": {"sign_in.html": SIGN_IN_HTML, "error.html": ERROR_HTML},
    }
