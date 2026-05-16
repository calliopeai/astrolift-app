"""SCM provider services — higher-level orchestrations on top of the
per-host driver modules. The drivers in ``astrolift_scm/providers``
own the wire protocol; this package owns the policy: which
connection to use, which file path to target, how to surface a
"workflow missing" condition cleanly back to the resolver layer.
"""
