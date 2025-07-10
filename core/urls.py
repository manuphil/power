# urls.py (fichier principal du projet)
from django.contrib import admin
from django.urls import path, include, re_path
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

# Configuration Swagger
schema_view = get_schema_view(
    openapi.Info(
        title="🍷 Winery Ball Lottery on Solana API",
        default_version='v1',
        description="""
        **Winery Ball Lottery on Solana** - API Documentation
        
        🎰 **Système de loterie décentralisé sur Solana**
        
        ## Fonctionnalités principales:
        - 🪙 Gestion des tokens $BALL
        - 🎫 Système de tickets basé sur les holdings
        - ⏰ Tirages horaires et journaliers
        - 💰 Distribution automatique des jackpots
        - 🔐 Intégration wallet Solana
        - 📊 Suivi des transactions blockchain
        
        ## Types de loterie:
        - **Hourly PowerBall**: Tirages toutes les heures
        - **Mega Daily PowerBall**: Grand tirage quotidien
        
        ## Système de tickets:
        - 1 ticket = 10,000 tokens $BALL
        - Plus de tokens = plus de chances de gagner
        
        ---
        *Powered by Solana Blockchain* ⚡
        """,
        terms_of_service="https://www.yourapp.com/terms/",
        contact=openapi.Contact(
            name="Winery Ball Lottery Support",
            email="support@wineryball.com"
        ),
        license=openapi.License(name="MIT License"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('base.urls')),
    
    # Documentation Swagger/OpenAPI
    re_path(r'^swagger(?P<format>\.json|\.yaml)$', 
            schema_view.without_ui(cache_timeout=0), 
            name='schema-json'),
    
    re_path(r'^swagger/$', 
            schema_view.with_ui('swagger', cache_timeout=0), 
            name='schema-swagger-ui'),
    
    re_path(r'^redoc/$', 
            schema_view.with_ui('redoc', cache_timeout=0), 
            name='schema-redoc'),
    
    # API endpoints
    path('api/v1/', include('base.urls')),  # Si vous avez des API endpoints
]