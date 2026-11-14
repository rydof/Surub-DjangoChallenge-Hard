import os

from celery import Celery
from django.core.signals import setting_changed

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'coderun2026_starter.settings')

app = Celery('coderun2026_starter')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


def _sync_celery_eager(setting, value, **kwargs):
    if setting == 'CELERY_TASK_ALWAYS_EAGER':
        app.conf.task_always_eager = bool(value)


setting_changed.connect(_sync_celery_eager)
