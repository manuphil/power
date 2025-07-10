from celery.schedules import crontab
from django.conf import settings

# Configuration des tâches périodiques pour la production
CELERY_BEAT_SCHEDULE = {
    # Synchronisation de l'état de la loterie toutes les 15 secondes
    'sync-lottery-state': {
        'task': 'base.tasks.sync_lottery_state',
        'schedule': 15.0,
        'options': {
            'expires': 30,
            'retry': True,
            'retry_policy': {
                'max_retries': 3,
                'interval_start': 0,
                'interval_step': 0.2,
                'interval_max': 0.2,
            }
        }
    },
    
    # Synchronisation des participants toutes les 30 secondes
    'sync-participant-holdings': {
        'task': 'base.tasks.sync_participant_holdings',
        'schedule': 30.0,
        'options': {
            'expires': 60,
            'retry': True,
            'retry_policy': {
                'max_retries': 2,
                'interval_start': 0,
                'interval_step': 0.5,
                'interval_max': 1.0,
            }
        }
    },
    
    # Surveillance des événements blockchain toutes les 10 secondes
    'monitor-blockchain-events': {
        'task': 'base.tasks.monitor_blockchain_events',
        'schedule': 10.0,
        'options': {
            'expires': 20,
            'retry': True,
            'retry_policy': {
                'max_retries': 5,
                'interval_start': 0,
                'interval_step': 0.1,
                'interval_max': 0.5,
            }
        }
    },
    
    # Création des tirages programmés toutes les 5 minutes
    'create-scheduled-lotteries': {
        'task': 'base.tasks.create_scheduled_lotteries',
        'schedule': 300.0,  # 5 minutes
        'options': {
            'expires': 600,
            'retry': True,
            'retry_policy': {
                'max_retries': 3,
                'interval_start': 0,
                'interval_step': 1.0,
                'interval_max': 5.0,
            }
        }
    },
    
    # Exécution des tirages en attente toutes les 30 secondes
    'execute-pending-lotteries': {
        'task': 'base.tasks.execute_pending_lotteries',
        'schedule': 30.0,
        'options': {
            'expires': 60,
            'retry': True,
            'retry_policy': {
                'max_retries': 2,
                'interval_start': 0,
                'interval_step': 1.0,
                'interval_max': 3.0,
            }
        }
    },
    
    # Traitement des paiements toutes les 45 secondes
    'process-pending-payouts': {
        'task': 'base.tasks.process_pending_payouts',
        'schedule': 45.0,
        'options': {
            'expires': 90,
            'retry': True,
            'retry_policy': {
                'max_retries': 3,
                'interval_start': 0,
                'interval_step': 2.0,
                'interval_max': 10.0,
            }
        }
    },
    
    # Mise à jour des pools de jackpot toutes les 20 secondes
    'update-jackpot-pools': {
        'task': 'base.tasks.update_jackpot_pools',
        'schedule': 20.0,
        'options': {
            'expires': 40,
            'retry': True,
            'retry_policy': {
                'max_retries': 2,
                'interval_start': 0,
                'interval_step': 0.5,
                'interval_max': 1.0,
            }
        }
    },
    
    # Surveillance des transactions Raydium toutes les 5 secondes
    'monitor-raydium-transactions': {
        'task': 'base.tasks.monitor_raydium_transactions',
        'schedule': 5.0,
        'options': {
            'expires': 10,
            'retry': True,
            'retry_policy': {
                'max_retries': 3,
                'interval_start': 0,
                'interval_step': 0.1,
                'interval_max': 0.3,
            }
        }
    },
    
    # Synchronisation des balances de tokens toutes les 60 secondes
    'sync-token-balances': {
        'task': 'base.tasks.sync_token_balances',
        'schedule': 60.0,
        'options': {
            'expires': 120,
            'retry': True,
            'retry_policy': {
                'max_retries': 2,
                'interval_start': 0,
                'interval_step': 1.0,
                'interval_max': 3.0,
            }
        }
    },
    
    # Vérification de santé système toutes les 2 minutes
    'health-check': {
        'task': 'base.tasks.health_check',
        'schedule': 120.0,
        'options': {
            'expires': 240,
            'retry': False,
        }
    },
    
    # Nettoyage des données anciennes tous les jours à 3h du matin
    'cleanup-old-data': {
        'task': 'base.tasks.cleanup_old_data',
        'schedule': crontab(hour=3, minute=0),
        'options': {
            'expires': 3600,
            'retry': True,
            'retry_policy': {
                'max_retries': 1,
                'interval_start': 0,
                'interval_step': 60.0,
                'interval_max': 300.0,
            }
        }
    },
    
    # Génération de rapports tous les jours à 2h du matin
    'generate-daily-reports': {
        'task': 'base.tasks.generate_daily_reports',
        'schedule': crontab(hour=2, minute=0),
        'options': {
            'expires': 3600,
            'retry': True,
            'retry_policy': {
                'max_retries': 2,
                'interval_start': 0,
                'interval_step': 300.0,
                'interval_max': 900.0,
            }
        }
    },
    
    # Sauvegarde des métriques toutes les 10 minutes
    'save-metrics': {
        'task': 'base.tasks.save_metrics',
        'schedule': 600.0,  # 10 minutes
        'options': {
            'expires': 1200,
            'retry': True,
            'retry_policy': {
                'max_retries': 1,
                'interval_start': 0,
                'interval_step': 30.0,
                'interval_max': 60.0,
            }
        }
    },
    
    # Validation des données critiques toutes les 5 minutes
    'validate-critical-data': {
        'task': 'base.tasks.validate_critical_data',
        'schedule': 300.0,
        'options': {
            'expires': 600,
            'retry': True,
            'retry_policy': {
                'max_retries': 2,
                'interval_start': 0,
                'interval_step': 10.0,
                'interval_max': 30.0,
            }
        }
    },
    
    # Notifications en temps réel toutes les 15 secondes
    'send-realtime-notifications': {
        'task': 'base.tasks.send_realtime_notifications',
        'schedule': 15.0,
        'options': {
            'expires': 30,
            'retry': True,
            'retry_policy': {
                'max_retries': 1,
                'interval_start': 0,
                'interval_step': 1.0,
                'interval_max': 2.0,
            }
        }
    },
}

# Configuration Celery pour la production
CELERY_TIMEZONE = 'UTC'
CELERY_ENABLE_UTC = True

# Configuration des queues pour la production
CELERY_TASK_ROUTES = {
    'base.tasks.sync_lottery_state': {'queue': 'high_priority'},
    'base.tasks.monitor_blockchain_events': {'queue': 'high_priority'},
    'base.tasks.execute_pending_lotteries': {'queue': 'critical'},
    'base.tasks.process_pending_payouts': {'queue': 'critical'},
    'base.tasks.monitor_raydium_transactions': {'queue': 'high_priority'},
    'base.tasks.sync_participant_holdings': {'queue': 'medium_priority'},
    'base.tasks.sync_token_balances': {'queue': 'medium_priority'},
    'base.tasks.update_jackpot_pools': {'queue': 'medium_priority'},
    'base.tasks.send_realtime_notifications': {'queue': 'low_priority'},
    'base.tasks.health_check': {'queue': 'low_priority'},
    'base.tasks.cleanup_old_data': {'queue': 'maintenance'},
    'base.tasks.generate_daily_reports': {'queue': 'maintenance'},
    'base.tasks.save_metrics': {'queue': 'maintenance'},
    'base.tasks.validate_critical_data': {'queue': 'medium_priority'},
}

# Configuration des priorités
CELERY_TASK_DEFAULT_PRIORITY = 5
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_DISABLE_RATE_LIMITS = False

# Configuration de la sérialisation
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']

# Configuration des résultats
CELERY_RESULT_EXPIRES = 3600  # 1 heure
CELERY_TASK_IGNORE_RESULT = False
CELERY_STORE_ERRORS_EVEN_IF_IGNORED = True

# Configuration de la surveillance
CELERY_SEND_TASK_EVENTS = True
CELERY_SEND_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True

# Configuration des limites
CELERY_TASK_TIME_LIMIT = 300  # 5 minutes
CELERY_TASK_SOFT_TIME_LIMIT = 240  # 4 minutes

# Configuration spécifique pour la production
if not getattr(settings, 'DEBUG', False):
    # En production, utiliser des intervalles plus courts pour la réactivité
    CELERY_BEAT_SCHEDULE.update({
        'sync-lottery-state': {
            **CELERY_BEAT_SCHEDULE['sync-lottery-state'],
            'schedule': 10.0,  # Plus fréquent en production
        },
        'monitor-blockchain-events': {
            **CELERY_BEAT_SCHEDULE['monitor-blockchain-events'],
            'schedule': 5.0,  # Plus fréquent en production
        },
        'execute-pending-lotteries': {
            **CELERY_BEAT_SCHEDULE['execute-pending-lotteries'],
            'schedule': 15.0,  # Plus fréquent en production
        },
    })

# Configuration des logs pour la production
CELERY_WORKER_LOG_FORMAT = '[%(asctime)s: %(levelname)s/%(processName)s] %(message)s'
CELERY_WORKER_TASK_LOG_FORMAT = '[%(asctime)s: %(levelname)s/%(processName)s][%(task_name)s(%(task_id)s)] %(message)s'

# Configuration de la surveillance des erreurs
CELERY_ANNOTATIONS = {
    '*': {
        'rate_limit': '100/m',  # Limite globale
    },
    'base.tasks.monitor_blockchain_events': {
        'rate_limit': '1000/m',  # Plus élevé pour la surveillance blockchain
    },
    'base.tasks.monitor_raydium_transactions': {
        'rate_limit': '1000/m',  # Plus élevé pour Raydium
    },
    'base.tasks.sync_lottery_state': {
        'rate_limit': '200/m',  # Élevé pour la synchronisation d'état
    },
}
