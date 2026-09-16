from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class AgentTaskInputReply(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    request_event = models.OneToOneField(
        "astrolift_agents.AgentTaskEvent", related_name="input_reply", on_delete=models.CASCADE
    )
    response = models.JSONField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", null=True, on_delete=models.SET_NULL
    )
    author_label = models.CharField(max_length=255, blank=True, default="")
