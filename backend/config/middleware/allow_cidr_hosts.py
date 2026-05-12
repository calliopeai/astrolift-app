from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http.request import validate_host
from netaddr import IPAddress, IPNetwork


class AllowCIDRHostsMiddleware:
    KNOWN_RANGES = {
        IPNetwork("10.0.0.0/8"),
    }

    KNOWN_DOMAINS = {
        ".amazonaws.com",
        ".localhost",
        ".local",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.CONFIGURATION.lower() == "local" or settings.CONFIGURATION.lower() == "tests":
            return self.get_response(request)

        host = request.get_host().split(":")[0]
        # Delegate to Django's built-in matcher so '*' and '.example.com'-style
        # wildcards in ALLOWED_HOSTS work the same way they do everywhere else
        # in Django. Plain `host in ALLOWED_HOSTS` only catches exact literals
        # and silently fails on the very wildcards ALLOWED_HOSTS is designed
        # to support.
        if validate_host(host, settings.ALLOWED_HOSTS):
            return self.get_response(request)
        try:
            for domain in self.KNOWN_DOMAINS:
                if host.endswith(domain):
                    break
            else:
                IPAddress(host)  # Allow any IP Addresses
        except Exception as exc:
            raise DisallowedHost(f"Invalid host: {host}") from exc
        return self.get_response(request)
