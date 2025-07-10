import logging
import time
import json
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from django.core.cache import cache
from .models import AuditLog

logger = logging.getLogger(__name__)

class APILoggingMiddleware(MiddlewareMixin):
    """Middleware pour logger les requêtes API"""
    
    def process_request(self, request):
        # Enregistrer le temps de début
        request.start_time = time.time()
        
        # Logger les requêtes API importantes
        if request.path.startswith('/api/'):
            logger.info(f"API Request: {request.method} {request.path} from {request.META.get('REMOTE_ADDR')}")
    
    def process_response(self, request, response):
        # Calculer le temps de traitement
        if hasattr(request, 'start_time'):
            duration = time.time() - request.start_time
            
            # Logger les réponses lentes
            if duration > 1.0:  # Plus d'1 seconde
                logger.warning(f"Slow API Response: {request.method} {request.path} took {duration:.2f}s")
        
        return response

class RateLimitMiddleware(MiddlewareMixin):
    """Middleware pour limiter le taux de requêtes"""
    
    def process_request(self, request):
        # Ignorer les requêtes non-API
        if not request.path.startswith('/api/'):
            return None
        
        # Obtenir l'IP du client
        client_ip = request.META.get('REMOTE_ADDR')
        if not client_ip:
            return None
        
        # Clé de cache pour cette IP
        cache_key = f"rate_limit:{client_ip}"
        
        # Obtenir le nombre de requêtes actuelles
        current_requests = cache.get(cache_key, 0)
        
        # Limite: 100 requêtes par minute
        if current_requests >= 100:
            logger.warning(f"Rate limit exceeded for IP {client_ip}")
            return JsonResponse({
                'error': 'Trop de requêtes. Veuillez réessayer plus tard.',
                'code': 'RATE_LIMIT_EXCEEDED'
            }, status=429)
        
        # Incrémenter le compteur
        cache.set(cache_key, current_requests + 1, 60)  # Expire après 1 minute
        
        return None

class ErrorHandlingMiddleware(MiddlewareMixin):
    """Middleware pour gérer les erreurs globales"""
    
    def process_exception(self, request, exception):
        # Logger l'erreur
        logger.error(f"Unhandled exception in {request.path}: {exception}", exc_info=True)
        
        # Créer un log d'audit pour les erreurs critiques
        if request.path.startswith('/api/'):
            try:
                AuditLog.objects.create(
                    action_type='system_error',
                    description=f'Erreur non gérée: {str(exception)}',
                    user=request.user if request.user.is_authenticated else None,
                    metadata={
                        'path': request.path,
                        'method': request.method,
                        'error_type': type(exception).__name__,
                        'error_message': str(exception)
                    },
                    ip_address=request.META.get('REMOTE_ADDR')
                )
            except Exception as e:
                logger.error(f"Failed to create audit log: {e}")
        
        # Retourner une réponse JSON pour les API
        if request.path.startswith('/api/'):
            return JsonResponse({
                'error': 'Une erreur interne s\'est produite',
                'code': 'INTERNAL_ERROR'
            }, status=500)
        
        return None

class MaintenanceMiddleware(MiddlewareMixin):
    """Middleware pour le mode maintenance"""
    
    def process_request(self, request):
        # Vérifier si le mode maintenance est activé
        maintenance_mode = cache.get('maintenance_mode', False)
        
        if maintenance_mode:
            # Permettre l'accès aux admins
            if request.user.is_authenticated and request.user.is_staff:
                return None
            
            # Permettre l'accès aux endpoints de santé
            if request.path in ['/api/v1/health/', '/admin/']:
                return None
            
            # Bloquer les autres requêtes
            if request.path.startswith('/api/'):
                return JsonResponse({
                    'error': 'Service temporairement indisponible pour maintenance',
                    'code': 'MAINTENANCE_MODE'
                }, status=503)
        
        return None

class CORSMiddleware(MiddlewareMixin):
    """Middleware CORS personnalisé"""
    
    def process_response(self, request, response):
        # Ajouter les headers CORS pour les API
        if request.path.startswith('/api/'):
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
            response['Access-Control-Max-Age'] = '86400'
        
        return response
    
    def process_request(self, request):
        # Gérer les requêtes OPTIONS (preflight)
        if request.method == 'OPTIONS':
            response = JsonResponse({})
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
            response['Access-Control-Max-Age'] = '86400'
            return response
        
        return None