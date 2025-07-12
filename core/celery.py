import os
from celery import Celery
from django.conf import settings

# Définir le module de configuration Django par défaut pour le programme 'celery'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('lottery_backend')

# Utiliser une chaîne ici signifie que le worker n'a pas besoin de sérialiser
# l'objet de configuration vers les processus enfants.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Charger les modules de tâches de toutes les applications Django enregistrées
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

# Configuration supplémentaire pour la production
if not settings.DEBUG:
    # Configuration optimisée pour la production
    app.conf.update(
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        worker_disable_rate_limits=False,
        task_reject_on_worker_lost=True,
        task_ignore_result=False,
        result_expires=3600,
        worker_max_tasks_per_child=1000,
        worker_max_memory_per_child=200000,  # 200MB
    )