from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from .views import (
    UserViewSet, TokenHoldingViewSet, LotteryViewSet,
    WinnerViewSet, TransactionViewSet, JackpotPoolViewSet,
    DashboardViewSet, StatsViewSet, WalletInfoViewSet,
    SystemConfigViewSet, AuditLogViewSet
)

# ================================
# 📦 API ROUTER CONFIGURATION
# ================================
router = DefaultRouter()

# Ressources REST principales
router.register(r'users', UserViewSet)
router.register(r'holdings', TokenHoldingViewSet)
router.register(r'lotteries', LotteryViewSet)
router.register(r'winners', WinnerViewSet)
router.register(r'transactions', TransactionViewSet)
router.register(r'jackpots', JackpotPoolViewSet)
router.register(r'audit-logs', AuditLogViewSet)

# Vues personnalisées avec basename (si `.queryset` n’est pas défini dans la vue)
router.register(r'dashboard', DashboardViewSet, basename='dashboard')
router.register(r'stats', StatsViewSet, basename='stats')
router.register(r'wallet-info', WalletInfoViewSet, basename='wallet-info')
router.register(r'config', SystemConfigViewSet)

# ================================
# 🔗 URL PATTERNS
# ================================
urlpatterns = [
    # 🌐 API Endpoints v1
    path('api/v1/', include(router.urls)),

    # 🔐 JWT Authentication
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/token/verify/', TokenVerifyView.as_view(), name='token_verify'),

    
    # ⚡ WebSocket routing (optionnel - nécessite Django Channels)
    # path('ws/', include('channels.routing')),
  

]
