"""Astrolift Azure provider plugin.

This package intentionally shares Microsoft's ``azure`` namespace so the
existing public plugin imports (``azure.plugin``) remain stable.  Extend the
package search path to include the PEP 420 namespace portions installed by
``azure-identity``, ``azure-mgmt-*``, and the Azure data-plane clients;
otherwise this regular package shadows ``azure.identity`` and every live
driver fails while fake-client unit tests continue to pass.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
