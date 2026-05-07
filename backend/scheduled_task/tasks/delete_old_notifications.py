import logging
from datetime import timedelta
from typing import Optional

from core.models import Notification
from django.utils import timezone
from django.utils.functional import classproperty
from scheduled_task.tasks import BaseTask

logger = logging.getLogger(__name__)


class Task(BaseTask):

    @classproperty
    def schedule(cls) -> Optional[dict]:
        return {
            'minute': '0',
            'hour': '3',
            'day_of_week': '*',
            'day_of_month': '*',
            'month_of_year': '*',
        }

    def run(self):
        logger.info("Deleting notifications older than 30 days")
        batch_size = 1000
        thirty_days_ago = timezone.now() - timedelta(days=30)

        records = Notification.objects.filter(created_at__lt=thirty_days_ago)[:batch_size]
        while records.count() > 0:
            Notification.objects.filter(id__in=records).delete()
            records = Notification.objects.filter(created_at__lt=thirty_days_ago)[:batch_size]

        return {
            self.name: "succeeded"
        }
