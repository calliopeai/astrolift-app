"""Astrolift Temporal workflows + activities.

Skeletons that mirror the catalog in ``specs/06-workflows-temporal.md``.
The activities here are typed signatures + no-op bodies (or thin
delegations to the platform models); concrete cloud calls live in
the provider-plugin packages and are dispatched through
``astrolift_drivers``.

Workers register everything in this package by importing
``astrolift_workflows.worker.WORKFLOWS`` /
``astrolift_workflows.worker.ACTIVITIES`` and passing them to the
Temporal Worker constructor.
"""
