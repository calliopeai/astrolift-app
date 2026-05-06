"""Temporal worker bootstrap for Astrolift.

Starts a Temporal worker that registers all workflow definitions and
activities. Run as a standalone process alongside the Django API server.

Usage:
    python -m workflows.temporal.worker

The worker connects to the Temporal server specified by TEMPORAL_ADDRESS
and listens on the task queue specified by TEMPORAL_TASK_QUEUE.
"""
import asyncio
import logging
import os
import sys

from temporalio.client import Client
from temporalio.worker import Worker

logger = logging.getLogger(__name__)

# Default configuration -- overridden by Django settings when available
TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "astrolift-main")


def _collect_workflows():
    """Import and return all workflow classes from definitions/."""
    workflows = []
    # Workflow classes will be registered here as they are built.
    # Example:
    #   from workflows.temporal.definitions.onboard_app import OnboardAppWorkflow
    #   workflows.append(OnboardAppWorkflow)
    return workflows


def _collect_activities():
    """Import and return all activity functions from activities/."""
    activities = []
    # Activity functions will be registered here as they are built.
    # Example:
    #   from workflows.temporal.activities.provisioning import provision_namespace
    #   activities.append(provision_namespace)
    return activities


async def run_worker():
    """Connect to Temporal and run the worker until interrupted."""
    logger.info(
        "Connecting to Temporal at %s (namespace=%s, queue=%s)",
        TEMPORAL_ADDRESS,
        TEMPORAL_NAMESPACE,
        TEMPORAL_TASK_QUEUE,
    )

    client = await Client.connect(
        TEMPORAL_ADDRESS,
        namespace=TEMPORAL_NAMESPACE,
    )

    workflows = _collect_workflows()
    activities = _collect_activities()

    logger.info(
        "Starting worker with %d workflow(s) and %d activity(ies)",
        len(workflows),
        len(activities),
    )

    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=workflows,
        activities=activities,
    )

    await worker.run()


def main():
    """Entry point for the Temporal worker process."""
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
