"""Celery application.

Background jobs are used for one thing only: processing webhook deliveries
off the request/response cycle, with retries. If no broker is configured the
tasks run inline (``CELERY_TASK_ALWAYS_EAGER``) so the service is still
correct in a single-process deployment.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("eve")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:  # pragma: no cover - operational helper
    return f"request: {self.request!r}"
