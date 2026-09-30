import time
from datetime import datetime, timezone

from django.conf import settings
from django.http import HttpResponse, JsonResponse

_START_TIME = time.monotonic()


def _uptime_str():
    secs = int(time.monotonic() - _START_TIME)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def root_view(request):
    if request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({
            "service": "astrolift-api",
            "version": settings.VERSION,
            "status": "ok",
            "uptime": _uptime_str(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": getattr(settings, "DJANGO_CONFIGURATION", "unknown"),
            "links": {
                "admin": "/app/admin/",
                "graphql": "/app/gql/config/",
                "health": "/health/",
                "metrics": "/metrics",
            },
        })

    base = settings.BASE_URL
    version = settings.VERSION
    env = getattr(settings, "DJANGO_CONFIGURATION", "Local")
    uptime = _uptime_str()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    env_color = {
        "Local": "#22c55e",
        "Staging": "#f59e0b",
        "Production": "#ef4444",
    }.get(env, "#6b7280")

    links = [
        ("Admin", f"/{base}admin/", "Manage users, permissions and data"),
        ("GraphQL", f"/{base}gql/config/", "Interactive GraphQL explorer"),
        ("Health", "/health/", "Service health checks"),
        ("Metrics", "/metrics", "Prometheus metrics endpoint"),
    ]

    links_html = "\n".join(
        f"""<a href="{url}" class="link-card">
              <span class="link-title">{name}</span>
              <span class="link-desc">{desc}</span>
              <span class="link-arrow">→</span>
            </a>"""
        for name, url, desc in links
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Astrolift API</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg: #0a0a0a;
      --surface: #111111;
      --border: #1f1f1f;
      --text: #e5e5e5;
      --muted: #6b7280;
      --accent: #ffffff;
      --green: #22c55e;
    }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }}

    .container {{
      width: 100%;
      max-width: 560px;
      display: flex;
      flex-direction: column;
      gap: 2rem;
    }}

    /* Header */
    .header {{ display: flex; flex-direction: column; gap: 0.5rem; }}

    .wordmark {{
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: var(--accent);
    }}

    .tagline {{
      font-size: 0.875rem;
      color: var(--muted);
    }}

    /* Status bar */
    .status-bar {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1rem 1.25rem;
      display: flex;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
    }}

    .status-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 6px var(--green);
      flex-shrink: 0;
    }}

    .status-text {{
      font-size: 0.875rem;
      font-weight: 500;
      flex: 1;
    }}

    .meta-pills {{
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}

    .pill {{
      font-size: 0.75rem;
      padding: 0.2rem 0.6rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      color: var(--muted);
      white-space: nowrap;
    }}

    .pill-env {{
      border-color: {env_color}33;
      color: {env_color};
    }}

    /* Links grid */
    .links {{
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }}

    .links-label {{
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      padding: 0 0.25rem;
      margin-bottom: 0.25rem;
    }}

    .link-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 0.625rem;
      padding: 0.875rem 1.25rem;
      display: grid;
      grid-template-columns: 1fr auto;
      grid-template-rows: auto auto;
      gap: 0.125rem 0.5rem;
      text-decoration: none;
      color: inherit;
      transition: border-color 0.15s, background 0.15s;
    }}

    .link-card:hover {{
      border-color: #2f2f2f;
      background: #161616;
    }}

    .link-title {{
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--text);
      grid-column: 1;
      grid-row: 1;
    }}

    .link-desc {{
      font-size: 0.75rem;
      color: var(--muted);
      grid-column: 1;
      grid-row: 2;
    }}

    .link-arrow {{
      font-size: 1rem;
      color: var(--muted);
      grid-column: 2;
      grid-row: 1 / 3;
      align-self: center;
      transition: color 0.15s, transform 0.15s;
    }}

    .link-card:hover .link-arrow {{
      color: var(--text);
      transform: translateX(2px);
    }}

    /* Footer */
    .footer {{
      font-size: 0.7rem;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.25rem;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="wordmark">Astrolift</div>
      <div class="tagline">API server is running.</div>
    </div>

    <div class="status-bar">
      <div class="status-dot"></div>
      <div class="status-text">All systems operational</div>
      <div class="meta-pills">
        <span class="pill pill-env">{env}</span>
        <span class="pill">v{version}</span>
        <span class="pill">↑ {uptime}</span>
      </div>
    </div>

    <div class="links">
      <div class="links-label">Endpoints</div>
      {links_html}
    </div>

    <div class="footer">
      <span>astrolift-api</span>
      <span>{now}</span>
    </div>
  </div>
</body>
</html>"""

    return HttpResponse(html)


def app_root_view(request):
    base = settings.BASE_URL
    version = settings.VERSION
    env = getattr(settings, "DJANGO_CONFIGURATION", "Local")
    uptime = _uptime_str()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    env_color = {
        "Local": "#22c55e",
        "Staging": "#f59e0b",
        "Production": "#ef4444",
    }.get(env, "#6b7280")

    links = [
        ("Admin",    f"/{base}admin/",      "Django admin — users, permissions, data"),
        ("GraphQL",  f"/{base}gql/config/", "Interactive GraphQL explorer"),
        ("Auth",     f"/{base}auth1/login", "Auth1 session login"),
        ("Health",   "/health/",            "Service health checks"),
        ("Metrics",  "/metrics",            "Prometheus metrics"),
    ]

    links_html = "\n".join(
        f"""<a href="{url}" class="link-card">
              <span class="link-title">{name}</span>
              <span class="link-desc">{desc}</span>
              <span class="link-arrow">→</span>
            </a>"""
        for name, url, desc in links
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Astrolift -- App</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0a0a0a; --surface: #111111; --border: #1f1f1f;
      --text: #e5e5e5; --muted: #6b7280; --accent: #ffffff; --green: #22c55e;
    }}
    body {{
      background: var(--bg); color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh; display: flex; flex-direction: column;
      align-items: center; justify-content: center; padding: 2rem;
    }}
    .container {{ width: 100%; max-width: 560px; display: flex; flex-direction: column; gap: 2rem; }}
    .header {{ display: flex; flex-direction: column; gap: 0.5rem; }}
    .wordmark {{ font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; color: var(--accent); }}
    .tagline {{ font-size: 0.875rem; color: var(--muted); }}
    .status-bar {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 0.75rem;
      padding: 1rem 1.25rem; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
    }}
    .status-dot {{
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--green); box-shadow: 0 0 6px var(--green); flex-shrink: 0;
    }}
    .status-text {{ font-size: 0.875rem; font-weight: 500; flex: 1; }}
    .meta-pills {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
    .pill {{
      font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: 999px;
      border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
    }}
    .pill-env {{ border-color: {env_color}33; color: {env_color}; }}
    .links {{ display: flex; flex-direction: column; gap: 0.5rem; }}
    .links-label {{
      font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--muted);
      padding: 0 0.25rem; margin-bottom: 0.25rem;
    }}
    .link-card {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 0.625rem;
      padding: 0.875rem 1.25rem; display: grid;
      grid-template-columns: 1fr auto; grid-template-rows: auto auto;
      gap: 0.125rem 0.5rem; text-decoration: none; color: inherit;
      transition: border-color 0.15s, background 0.15s;
    }}
    .link-card:hover {{ border-color: #2f2f2f; background: #161616; }}
    .link-title {{
      font-size: 0.875rem; font-weight: 500; color: var(--text);
      grid-column: 1; grid-row: 1;
    }}
    .link-desc {{ font-size: 0.75rem; color: var(--muted); grid-column: 1; grid-row: 2; }}
    .link-arrow {{
      font-size: 1rem; color: var(--muted); grid-column: 2; grid-row: 1 / 3;
      align-self: center; transition: color 0.15s, transform 0.15s;
    }}
    .link-card:hover .link-arrow {{ color: var(--text); transform: translateX(2px); }}
    .footer {{ font-size: 0.7rem; color: var(--muted); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.25rem; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="wordmark">Astrolift <span style="font-weight:400;color:var(--muted)">/app</span></div>
      <div class="tagline">Backend application namespace.</div>
    </div>
    <div class="status-bar">
      <div class="status-dot"></div>
      <div class="status-text">All systems operational</div>
      <div class="meta-pills">
        <span class="pill pill-env">{env}</span>
        <span class="pill">v{version}</span>
        <span class="pill">↑ {uptime}</span>
      </div>
    </div>
    <div class="links">
      <div class="links-label">Endpoints</div>
      {links_html}
    </div>
    <div class="footer">
      <span>astrolift-api . /{base}</span>
      <span>{now}</span>
    </div>
  </div>
</body>
</html>"""

    return HttpResponse(html)


def metrics_view(request):
    from core.permissions import require_platform_operator

    require_platform_operator(getattr(request, "user", None))
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


def ratelimited(request, exception=None):
    """Response for a request django-ratelimit blocked (#1248).

    ``RatelimitMiddleware`` routes ``Ratelimited`` here. It pointed at
    Django's ``permission_denied`` view, so exhausting the GraphQL budget
    returned a bare **403** — indistinguishable, to both a client and a
    person reading devtools, from "you lost access to this resource". The
    browser console filled with `[Apollo] Network error: … status code 403`
    and the obvious reading was an auth failure.

    429 says what actually happened, and ``Retry-After`` says when to come
    back. The body is GraphQL-shaped because every rate-limited route here
    is a GraphQL endpoint, so a client that parses the payload gets a code
    it can branch on rather than a stack of HTML.
    """
    retry_after = getattr(settings, "RATELIMIT_RETRY_AFTER_SECONDS", 60)
    response = JsonResponse(
        {
            "errors": [
                {
                    "message": (
                        "Rate limit exceeded. This is a request-budget limit, "
                        "not a permission failure — retry after the window rolls."
                    ),
                    "extensions": {"code": "RATE_LIMITED", "retryAfterSeconds": retry_after},
                }
            ]
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after)
    return response
