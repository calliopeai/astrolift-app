"""Service-layer helpers for the registry app.

Code that's complex enough to test in isolation from a GraphQL
resolver lives here. The mutations + queries import these helpers
and stay focused on input shaping + audit/permission scaffolding.
"""
