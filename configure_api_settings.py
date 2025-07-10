#!/usr/bin/env python3
"""
Configuration complète des settings pour l'API
"""
import os
import re

def find_settings_file():
    """Trouve le fichier settings.py"""
    possible_paths = [
        './core/settings.py',
        './config/settings.py', 
        './settings.py',
        './lottery_solana/settings.py'
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    # Recherche récursive
    for root, dirs, files in os.walk('.'):
        if 'settings.py' in files:
            return os.path.join(root, 'settings.py')
    
    return None

def backup_settings(settings_file):
    """Crée une sauvegarde du fichier settings"""
    backup_file = f"{settings_file}.backup"
    with open(settings_file, 'r') as f:
        content = f.read()
    with open(backup_file, 'w') as f:
        f.write(content)
    print(f"✅ Sauvegarde créée: {backup_file}")

def configure_settings():
    """Configure complètement les settings"""
    settings_file = find_settings_file()
    
    if not settings_file:
        print("❌ Fichier settings.py non trouvé")
        return False
    
    print(f"📁 Fichier settings trouvé: {settings_file}")
    
    # Sauvegarde
    backup_settings(settings_file)
    
    # Lire le contenu
    with open(settings_file, 'r') as f:
        content = f.read()
    
    # Configuration à ajouter
    api_config = '''

# ============================================================================
# CONFIGURATION API REST & JWT
# ============================================================================

# Apps requis pour l'API
API_REQUIRED_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_filters',
    'corsheaders',
]

# Ajouter les apps s'ils ne sont pas déjà présents
for app in API_REQUIRED_APPS:
    if app not in INSTALLED_APPS:
        INSTALLED_APPS.append(app)

# Configuration REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'base.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour'
    },
    'EXCEPTION_HANDLER': 'rest_framework.views.exception_handler',
    'TEST_REQUEST_DEFAULT_FORMAT': 'json',
}

# Configuration JWT
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    'AUDIENCE': None,
    'ISSUER': None,
    'JWK_URL': None,
    'LEEWAY': 0,
    
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'USER_AUTHENTICATION_RULE': 'rest_framework_simplejwt.authentication.default_user_authentication_rule',
    
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    'TOKEN_USER_CLASS': 'rest_framework_simplejwt.models.TokenUser',
    
    'JTI_CLAIM': 'jti',
    
    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(minutes=5),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
}

# Configuration CORS (si nécessaire)
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

CORS_ALLOW_CREDENTIALS = True

# Middleware CORS
if 'corsheaders.middleware.CorsMiddleware' not in MIDDLEWARE:
    MIDDLEWARE.insert(0, 'corsheaders.middleware.CorsMiddleware')

# ============================================================================
# CONFIGURATION SOLANA (si pas déjà présent)
# ============================================================================

# Configuration Solana
SOLANA_RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.devnet.solana.com')
SOLANA_PROGRAM_ID = os.getenv('SOLANA_PROGRAM_ID', '2wqFWNXDYT2Q71ToNFBqKpV4scKSi1cjMuqVcT2jgruV')
SOLANA_ADMIN_PUBLIC_KEY = os.getenv('SOLANA_ADMIN_PUBLIC_KEY', 'Gb2uoQRXeM2qci4hcdYzAJMRsV3ZVcHbgPFBKKfWGcvh')
SOLANA_ADMIN_PRIVATE_KEY = os.getenv('SOLANA_ADMIN_PRIVATE_KEY', '')

# Configuration du cache (pour les performances)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
        'TIMEOUT': 300,
        'OPTIONS': {
            'MAX_ENTRIES': 1000,
        }
    }
}

# Logging pour debug
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'base': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'rest_framework': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}
'''
    
    # Supprimer les anciennes configurations s'il y en a
    patterns_to_remove = [
        r'REST_FRAMEWORK\s*=\s*{[^}]*}',
        r'SIMPLE_JWT\s*=\s*{[^}]*}',
        r'CORS_ALLOWED_ORIGINS\s*=\s*\[[^\]]*\]',
    ]
    
    for pattern in patterns_to_remove:
        content = re.sub(pattern, '', content, flags=re.DOTALL)
    
    # Ajouter la nouvelle configuration
    content += api_config
    
    # Sauvegarder
    with open(settings_file, 'w') as f:
        f.write(content)
    
    print("✅ Configuration API ajoutée au fichier settings")
    return True

if __name__ == "__main__":
    print("🔧 CONFIGURATION DES SETTINGS API")
    print("=" * 40)
    
    if configure_settings():
        print("\n✅ Configuration terminée!")
        print("\n📝 Prochaines étapes:")
        print("1. pip install djangorestframework djangorestframework-simplejwt django-filter django-cors-headers")
        print("2. python manage.py migrate")
        print("3. python manage.py runserver")
        print("4. Tester les endpoints")
    else:
        print("\n❌ Erreur lors de la configuration")
