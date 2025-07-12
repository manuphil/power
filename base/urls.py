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

# Vues personnalisées avec basename (si `.queryset` n'est pas défini dans la vue)
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

    # 👤 User Custom Actions
    path('api/v1/users/me/', 
         UserViewSet.as_view({'get': 'me'}), 
         name='user-me'),
    
    path('api/v1/users/<int:pk>/connect-wallet/', 
         UserViewSet.as_view({'post': 'connect_wallet'}), 
         name='user-connect-wallet'),

    # 🎫 Token Holdings Custom Actions
    path('api/v1/holdings/leaderboard/', 
         TokenHoldingViewSet.as_view({'get': 'leaderboard'}), 
         name='holdings-leaderboard'),
    
    path('api/v1/holdings/my-holdings/', 
         TokenHoldingViewSet.as_view({'get': 'my_holdings'}), 
         name='holdings-my-holdings'),
    
    path('api/v1/holdings/sync-wallet/', 
         TokenHoldingViewSet.as_view({'post': 'sync_wallet'}), 
         name='holdings-sync-wallet'),
    
    path('api/v1/holdings/sync-all/', 
         TokenHoldingViewSet.as_view({'post': 'sync_all'}), 
         name='holdings-sync-all'),

    # 🎲 Lottery Custom Actions
    path('api/v1/lotteries/upcoming/', 
         LotteryViewSet.as_view({'get': 'upcoming'}), 
         name='lotteries-upcoming'),
    
    path('api/v1/lotteries/recent/', 
         LotteryViewSet.as_view({'get': 'recent'}), 
         name='lotteries-recent'),
    
    path('api/v1/lotteries/<int:pk>/execute/', 
         LotteryViewSet.as_view({'post': 'execute'}), 
         name='lottery-execute'),
    
    path('api/v1/lotteries/<int:pk>/sync-with-solana/', 
         LotteryViewSet.as_view({'post': 'sync_with_solana'}), 
         name='lottery-sync-solana'),

    # 🏆 Winner Custom Actions
    path('api/v1/winners/hall-of-fame/', 
         WinnerViewSet.as_view({'get': 'hall_of_fame'}), 
         name='winners-hall-of-fame'),
    
    path('api/v1/winners/my-wins/', 
         WinnerViewSet.as_view({'get': 'my_wins'}), 
         name='winners-my-wins'),
    
    path('api/v1/winners/<int:pk>/pay-winner/', 
         WinnerViewSet.as_view({'post': 'pay_winner'}), 
         name='winner-pay'),

    # 💰 Jackpot Pool Custom Actions
    path('api/v1/jackpots/current-pools/', 
         JackpotPoolViewSet.as_view({'get': 'current_pools'}), 
         name='jackpots-current-pools'),
    
    path('api/v1/jackpots/sync-pools/', 
         JackpotPoolViewSet.as_view({'post': 'sync_pools'}), 
         name='jackpots-sync-pools'),

    # 💳 Transaction Custom Actions
    path('api/v1/transactions/recent-activity/', 
         TransactionViewSet.as_view({'get': 'recent_activity'}), 
         name='transactions-recent-activity'),
    
    path('api/v1/transactions/my-transactions/', 
         TransactionViewSet.as_view({'get': 'my_transactions'}), 
         name='transactions-my-transactions'),
    
    path('api/v1/transactions/stats/', 
         TransactionViewSet.as_view({'get': 'stats'}), 
         name='transactions-stats'),

    # 📊 Stats Custom Actions
    path('api/v1/stats/lottery-history/', 
         StatsViewSet.as_view({'get': 'lottery_history'}), 
         name='stats-lottery-history'),
    
    path('api/v1/stats/participant-stats/', 
         StatsViewSet.as_view({'get': 'participant_stats'}), 
         name='stats-participant-stats'),

    # ⚙️ System Config Custom Actions
    path('api/v1/config/public-config/', 
         SystemConfigViewSet.as_view({'get': 'public_config'}), 
         name='config-public-config'),
    
    path('api/v1/config/update-config/', 
         SystemConfigViewSet.as_view({'post': 'update_config'}), 
         name='config-update-config'),
    
    path('api/v1/config/solana-config/', 
         SystemConfigViewSet.as_view({'get': 'solana_config'}), 
         name='config-solana-config'),

    # 📋 Audit Log Custom Actions
    path('api/v1/audit-logs/recent-activity/', 
         AuditLogViewSet.as_view({'get': 'recent_activity'}), 
         name='audit-logs-recent-activity'),
    
    path('api/v1/audit-logs/user-activity/', 
         AuditLogViewSet.as_view({'get': 'user_activity'}), 
         name='audit-logs-user-activity'),

    # 📈 Dashboard Custom Actions
    path('api/v1/dashboard/trigger-sync/', 
         DashboardViewSet.as_view({'post': 'trigger_sync'}), 
         name='dashboard-trigger-sync'),
    
    path('api/v1/dashboard/system-status/', 
         DashboardViewSet.as_view({'get': 'system_status'}), 
         name='dashboard-system-status'),
    
    path('api/v1/dashboard/lottery-state/', 
         DashboardViewSet.as_view({'get': 'lottery_state'}), 
         name='dashboard-lottery-state'),
    
    path('api/v1/dashboard/stats/', 
         DashboardViewSet.as_view({'get': 'stats'}), 
         name='dashboard-stats'),

    # 👛 Wallet Info Custom Actions (with wallet address as parameter)
    path('api/v1/wallet-info/<str:pk>/', 
         WalletInfoViewSet.as_view({'get': 'retrieve'}), 
         name='wallet-info-detail'),
    
    path('api/v1/wallet-info/<str:pk>/sync-wallet/', 
         WalletInfoViewSet.as_view({'post': 'sync_wallet'}), 
         name='wallet-info-sync-wallet'),
    
    path('api/v1/wallet-info/leaderboard/', 
         WalletInfoViewSet.as_view({'get': 'leaderboard'}), 
         name='wallet-info-leaderboard'),
    
    path('api/v1/wallet-info/search/', 
         WalletInfoViewSet.as_view({'get': 'search'}), 
         name='wallet-info-search'),
    
    path('api/v1/wallet-info/<str:pk>/participation-history/', 
         WalletInfoViewSet.as_view({'get': 'participation_history'}), 
         name='wallet-info-participation-history'),
    
    path('api/v1/wallet-info/bulk-sync/', 
         WalletInfoViewSet.as_view({'get': 'bulk_sync'}), 
         name='wallet-info-bulk-sync'),

    # ⚡ WebSocket routing (optionnel - nécessite Django Channels)
    # path('ws/', include('channels.routing')),
]
