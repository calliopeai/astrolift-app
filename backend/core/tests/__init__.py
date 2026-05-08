import logging
from types import SimpleNamespace

from django.contrib.auth import get_user_model  # type: ignore
from django.test import TestCase

from config import settings

logger = logging.getLogger(__name__)


class TestBaseCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self):
        self.maxDiff = None

    @property
    def context(self):
        return SimpleNamespace(
            user=get_user_model().objects.get(username=settings.API_SYSTEM_USER), session={}
        )
