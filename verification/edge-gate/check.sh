#!/bin/bash
# Every app behind an install's login must refuse a request with no session.
#
#   ZONE=astro.example.net AUTH_HOST=auth.astro.example.net ./check.sh [slug ...]
#
# With no slugs, checks every active app `astro app list` returns, at
# https://<slug>.<ZONE>/. Each must answer a redirect whose Location is on
# AUTH_HOST or the identity provider (IDP_HOST_RE, default: cognito). A 200 is
# an open app; a redirect anywhere else (the app's own pages) is not proof of
# the gate. Hosts that do not answer (no public workload) are reported, not
# failed. PUBLIC="slug slug" names apps that are meant to be public.
# Run it before and after every edge or auth change; exit status is the
# number of apps that failed.
set -u
: "${ZONE:?set ZONE, the install's managed app zone}"
: "${AUTH_HOST:?set AUTH_HOST, the install's central auth host}"
IDP_HOST_RE=${IDP_HOST_RE:-amazoncognito\.com}
PUBLIC=" ${PUBLIC:-} "
if [ $# -gt 0 ]; then slugs="$*"; else
  slugs=$(astro app list --json | python3 -c '
import json, sys
d = json.load(sys.stdin)
items = d if isinstance(d, list) else next(v for v in d.values() if isinstance(v, list))
print(" ".join(a["slug"] for a in items if a.get("isActive") and not a.get("isArchived")))')
fi
fails=0
for slug in $slugs; do
  host="$slug.$ZONE"
  read -r code loc < <(curl -s -m 15 -o /dev/null -w "%{http_code} %{redirect_url}" "https://$host/")
  lochost=$(printf '%s' "$loc" | sed -E 's#^https?://([^/:]+).*#\1#')
  if [[ "$PUBLIC" == *" $slug "* ]]; then verdict="public ($code)"
  elif [[ "$code" =~ ^30[1278]$ ]] && { [ "$lochost" = "$AUTH_HOST" ] || [[ "$lochost" =~ $IDP_HOST_RE ]]; }; then verdict="gated"
  elif [ "$code" = 000 ] || [ "$code" = 404 ] || [ "$code" = 503 ]; then verdict="no route ($code)"
  else verdict="FAIL $code ${lochost:-no redirect}"; fails=$((fails + 1)); fi
  printf '%-28s %s\n' "$slug" "$verdict"
done
echo "failed=$fails"
exit "$fails"
