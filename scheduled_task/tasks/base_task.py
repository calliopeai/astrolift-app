"""Base class for scheduled tasks.

Tasks are registered by subclassing BaseTask. Each task defines a schedule
(as a cron dict) and a run() method. Tasks are executed via Django
management commands or Temporal scheduled workflows.
"""
import logging
from typing import Optional

import pydash
from django.apps.config import AppConfig
from django.utils.functional import classproperty

logger = logging.getLogger(__name__)


class BaseTask:
    """Base class for all scheduled tasks.

    Subclasses must be named 'Task' and define a run() method.
    Optionally define a schedule property returning a dict with
    cron fields (minute, hour, day_of_week, day_of_month, month_of_year).
    """

    TASK_CLASS_NAME = "Task"

    _tasks: dict[str, type] = {}

    def __new__(cls, *args, **kwargs):
        attr = '_instance'
        if not hasattr(cls, attr):
            instance = object.__new__(cls)
            setattr(cls, attr, instance)
        return getattr(cls, attr)

    def __init_subclass__(cls, **kwargs):
        if cls.__name__ == cls.TASK_CLASS_NAME:
            cls._tasks[cls.__module__] = cls

    def __call__(self, *args, **kwargs) -> None:
        return self.run(*args, **kwargs)

    def run(self, *args, **kwargs) -> None:
        raise NotImplementedError

    @classproperty
    def schedule(cls) -> Optional[dict]:
        """Return a cron schedule dict, or None if not scheduled.

        Example:
            return {'minute': '0', 'hour': '2', 'day_of_week': '*',
                    'day_of_month': '*', 'month_of_year': '*'}
        """
        return None

    @classproperty
    def name(cls) -> str:
        return pydash.human_case(cls.__module__.split('.')[-1])

    @classmethod
    def register_tasks(cls):
        """Log all registered tasks. Called from AppConfig.ready()."""
        for module, clazz in cls._tasks.items():
            logger.info(f'Registered scheduled task: {clazz.name} ({module})')

    @classmethod
    def post_migration(cls, app_config: AppConfig):
        """Hook for post-migration setup. Logs registered tasks."""
        for clazz in cls._tasks.values():
            logger.info(f"Task registered: {clazz.name}")

    @classmethod
    def run_all_due(cls):
        """Execute all scheduled tasks. Called from management command."""
        for module, clazz in cls._tasks.items():
            try:
                logger.info(f'Running task: {clazz.name}')
                result = clazz()()
                logger.info(f'Task {clazz.name} completed: {result}')
            except Exception as e:
                logger.exception(f'Task {clazz.name} failed: {e}')
