from django.shortcuts import render
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Sum, Count, Q, Avg
from django.core.cache import cache
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter

# Imports Solana
from .solana_service import solana_service
from .tasks import sync_lottery_state, sync_participant_holdings
import asyncio

from .models import (
    User, TokenHolding, Lottery, Winner, Transaction,
    JackpotPool, SystemConfig, AuditLog, LotteryType
)
from .serializers import (
    UserSerializer, TokenHoldingSerializer, LotteryListSerializer,
    LotteryDetailSerializer, WinnerSerializer, TransactionSerializer,
    JackpotPoolSerializer, DashboardSerializer, StatsSerializer,
    WalletInfoSerializer, LotteryCreateSerializer, SystemConfigSerializer
)
from .filters import LotteryFilter, TransactionFilter, WinnerFilter
from .pagination import StandardResultsSetPagination
from .permissions import IsOwnerOrReadOnly, IsAdminOrReadOnly
import logging

logger = logging.getLogger(__name__)

class UserViewSet(viewsets.ModelViewSet):
    """ViewSet pour la gestion des utilisateurs"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['username', 'wallet_address', 'email']
    ordering_fields = ['created_at', 'username']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Filtre les utilisateurs selon les permissions"""
        if self.request.user.is_staff:
            return User.objects.all()
        return User.objects.filter(id=self.request.user.id)
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """Informations de l'utilisateur connecté"""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def connect_wallet(self, request, pk=None):
        """Connecter un portefeuille à l'utilisateur"""
        user = self.get_object()
        wallet_address = request.data.get('wallet_address')
        
        if not wallet_address:
            return Response(
                {'error': 'Adresse de portefeuille requise'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Vérifier que le wallet n'est pas déjà utilisé
        if User.objects.filter(wallet_address=wallet_address).exclude(id=user.id).exists():
            return Response(
                {'error': 'Ce portefeuille est déjà connecté à un autre compte'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user.wallet_address = wallet_address
        user.save()
        
        # Synchroniser avec Solana
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            holding = loop.run_until_complete(
                solana_service.sync_participant(wallet_address)
            )
            
            loop.close()
            
            if holding:
                logger.info(f"Wallet {wallet_address} synchronized with Solana")
        except Exception as e:
            logger.error(f"Error syncing wallet {wallet_address} with Solana: {e}")
        
        # Log de l'action
        AuditLog.objects.create(
            action_type='wallet_connected',
            description=f'Portefeuille {wallet_address} connecté',
            user=user,
            wallet_address=wallet_address,
            ip_address=request.META.get('REMOTE_ADDR')
        )
        
        return Response({'success': 'Portefeuille connecté avec succès'})

class TokenHoldingViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les détentions de tokens"""
    queryset = TokenHolding.objects.all()
    serializer_class = TokenHoldingSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['is_eligible']
    ordering_fields = ['balance', 'tickets_count', 'last_updated']
    ordering = ['-tickets_count']
    
    @action(detail=False, methods=['get'])
    def leaderboard(self, request):
        """Classement des plus gros détenteurs"""
        top_holders = self.queryset.filter(
            is_eligible=True
        ).order_by('-tickets_count')[:100]
        
        serializer = self.get_serializer(top_holders, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_holdings(self, request):
        """Holdings de l'utilisateur connecté"""
        if not request.user.wallet_address:
            return Response(
                {'error': 'Aucun portefeuille connecté'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            holding = TokenHolding.objects.get(
                wallet_address=request.user.wallet_address
            )
            serializer = self.get_serializer(holding)
            return Response(serializer.data)
        except TokenHolding.DoesNotExist:
            return Response({
                'wallet_address': request.user.wallet_address,
                'balance': '0.00000000',
                'tickets_count': 0,
                'is_eligible': False,
                'last_updated': None
            })
    
    @action(detail=False, methods=['post'])
    def sync_wallet(self, request):
        """Synchronise un wallet spécifique avec Solana"""
        wallet_address = request.data.get('wallet_address')
        
        if not wallet_address:
            return Response(
                {'error': 'Adresse de wallet requise'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Vérifier les permissions
        if (wallet_address != request.user.wallet_address and 
            not request.user.is_staff):
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            result = loop.run_until_complete(
                solana_service.sync_participant(wallet_address)
            )
            
            loop.close()
            
            if result:
                serializer = self.get_serializer(result)
                return Response(serializer.data)
            else:
                return Response(
                    {'error': 'Impossible de synchroniser ce wallet'},
                    status=status.HTTP_404_NOT_FOUND
                )
                
        except Exception as e:
            logger.error(f"Error syncing wallet {wallet_address}: {e}")
            return Response(
                {'error': 'Erreur lors de la synchronisation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def sync_all(self, request):
        """Synchronise tous les participants (admin seulement)"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            # Déclencher la synchronisation en arrière-plan
            sync_participant_holdings.delay()
            
            return Response({'success': 'Synchronisation déclenchée'})
        except Exception as e:
            logger.error(f"Error triggering participant sync: {e}")
            return Response(
                {'error': 'Erreur lors du déclenchement'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class LotteryViewSet(viewsets.ModelViewSet):
    """ViewSet pour les tirages"""
    queryset = Lottery.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = LotteryFilter
    ordering_fields = ['scheduled_time', 'executed_time', 'jackpot_amount_sol']
    ordering = ['-scheduled_time']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return LotteryDetailSerializer
        elif self.action == 'create':
            return LotteryCreateSerializer
        return LotteryListSerializer
    
    def get_permissions(self):
        """Permissions selon l'action"""
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]
    
    def perform_create(self, serializer):
        """Crée un tirage et le synchronise avec Solana"""
        lottery = serializer.save()
        
        try:
            # Créer le tirage sur la blockchain
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            success = loop.run_until_complete(
                solana_service.create_lottery_on_chain(lottery)
            )
            
            loop.close()
            
            if not success:
                lottery.status = 'failed'
                lottery.save()
                logger.error(f"Failed to create lottery {lottery.id} on-chain")
            
        except Exception as e:
            logger.error(f"Error creating lottery on-chain: {e}")
            lottery.status = 'failed'
            lottery.save()
    
    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        """Prochains tirages"""
        upcoming_lotteries = self.queryset.filter(
            status='pending',
            scheduled_time__gt=timezone.now()
        ).order_by('scheduled_time')[:10]
        
        serializer = self.get_serializer(upcoming_lotteries, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Tirages récents"""
        recent_lotteries = self.queryset.filter(
            status='completed'
        ).order_by('-executed_time')[:20]
        
        serializer = self.get_serializer(recent_lotteries, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        """Exécuter un tirage (admin seulement)"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        lottery = self.get_object()
        
        if lottery.status != 'pending':
            return Response(
                {'error': 'Ce tirage ne peut pas être exécuté'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Sélectionner un gagnant
        eligible_participants = TokenHolding.objects.filter(
            is_eligible=True,
            tickets_count__gt=0
        )
        
        if not eligible_participants.exists():
            return Response(
                {'error': 'Aucun participant éligible'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Logique de sélection du gagnant (simplifiée)
        winner = self._select_winner(eligible_participants)
        
        try:
            # Exécuter sur la blockchain
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            success = loop.run_until_complete(
                solana_service.execute_lottery_on_chain(lottery, winner.wallet_address)
            )
            
            loop.close()
            
            if success:
                # Log de l'action
                AuditLog.objects.create(
                    action_type='lottery_executed',
                    description=f'Tirage {lottery.id} exécuté manuellement',
                    user=request.user,
                    lottery=lottery,
                    wallet_address=winner.wallet_address,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                
                return Response({
                    'success': 'Tirage exécuté avec succès',
                    'winner': winner.wallet_address
                })
            else:
                return Response(
                    {'error': 'Erreur lors de l\'exécution sur la blockchain'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution du tirage {lottery.id}: {e}")
            return Response(
                {'error': 'Erreur lors de l\'exécution'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def sync_with_solana(self, request, pk=None):
        """Synchronise un tirage avec Solana"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        lottery = self.get_object()
        
        try:
            # Déclencher la synchronisation
            sync_lottery_state.delay()
            
            return Response({'success': 'Synchronisation déclenchée'})
        except Exception as e:
            logger.error(f"Error triggering sync: {e}")
            return Response(
                {'error': 'Erreur lors de la synchronisation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _select_winner(self, participants):
        """Sélectionne un gagnant basé sur le nombre de tickets"""
        import random
        
        # Créer une liste pondérée basée sur les tickets
        weighted_participants = []
        for participant in participants:
            weighted_participants.extend([participant] * participant.tickets_count)
        
        if weighted_participants:
            return random.choice(weighted_participants)
        
        return participants.first()

class WinnerViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les gagnants"""
    queryset = Winner.objects.all()
    serializer_class = WinnerSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = WinnerFilter
    ordering_fields = ['created_at', 'winning_amount_sol']
    ordering = ['-created_at']
    
    @action(detail=False, methods=['get'])
    def hall_of_fame(self, request):
        """Hall of Fame des plus gros gains"""
        top_winners = self.queryset.order_by('-winning_amount_sol')[:50]
        serializer = self.get_serializer(top_winners, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_wins(self, request):
        """Gains de l'utilisateur connecté"""
        if not request.user.wallet_address:
            return Response([])
        
        my_wins = self.queryset.filter(
            wallet_address=request.user.wallet_address
        ).order_by('-created_at')
        
        serializer = self.get_serializer(my_wins, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def pay_winner(self, request, pk=None):
        """Payer un gagnant (admin seulement)"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        winner = self.get_object()
        
        if winner.payout_status != 'pending':
            return Response(
                {'error': 'Ce gagnant a déjà été payé ou est en cours de paiement'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Payer sur la blockchain
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            success = loop.run_until_complete(
                solana_service.pay_winner_on_chain(winner)
            )
            
            loop.close()
            
            if success:
                # Log de l'action
                AuditLog.objects.create(
                    action_type='payout_sent',
                    description=f'Gagnant {winner.wallet_address} payé manuellement',
                    user=request.user,
                    lottery=winner.lottery,
                    wallet_address=winner.wallet_address,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                
                return Response({'success': 'Paiement effectué avec succès'})
            else:
                return Response(
                    {'error': 'Erreur lors du paiement sur la blockchain'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        except Exception as e:
            logger.error(f"Error paying winner {winner.id}: {e}")
            return Response(
                {'error': 'Erreur lors du paiement'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les transactions"""
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = TransactionFilter
    ordering_fields = ['block_time', 'sol_amount', 'ball_amount']
    ordering = ['-block_time']
    
    @action(detail=False, methods=['get'])
    def recent_activity(self, request):
        """Activité récente"""
        recent_txs = self.queryset.order_by('-block_time')[:100]
        serializer = self.get_serializer(recent_txs, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_transactions(self, request):
        """Transactions de l'utilisateur"""
        if not request.user.wallet_address:
            return Response([])
        
        my_txs = self.queryset.filter(
            wallet_address=request.user.wallet_address
        ).order_by('-block_time')
        
        serializer = self.get_serializer(my_txs, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Statistiques des transactions"""
        cache_key = 'transaction_stats'
        stats = cache.get(cache_key)
        
        if not stats:
            # Volume total par type
            volume_by_type = self.queryset.values('transaction_type').annotate(
                total_sol=Sum('sol_amount'),
                total_ball=Sum('ball_amount'),
                count=Count('id')
            )
            
            # Contributions aux jackpots
            total_hourly_contributions = self.queryset.aggregate(
                total=Sum('hourly_jackpot_contribution')
            )['total'] or 0
            
            total_daily_contributions = self.queryset.aggregate(
                total=Sum('daily_jackpot_contribution')
            )['total'] or 0
            
            # Transactions récentes par jour
            from django.utils import timezone
            from datetime import timedelta
            
            last_7_days = timezone.now() - timedelta(days=7)
            daily_activity = self.queryset.filter(
                block_time__gte=last_7_days
            ).extra(
                select={'day': 'date(block_time)'}
            ).values('day').annotate(
                count=Count('id'),
                volume=Sum('sol_amount')
            ).order_by('day')
            
            stats = {
                'volume_by_type': list(volume_by_type),
                'total_hourly_contributions': str(total_hourly_contributions),
                'total_daily_contributions': str(total_daily_contributions),
                'daily_activity': list(daily_activity),
                'total_transactions': self.queryset.count()
            }
            
            cache.set(cache_key, stats, 300)  # Cache 5 minutes
        
        return Response(stats)

class JackpotPoolViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les pools de jackpot"""
    queryset = JackpotPool.objects.all()
    serializer_class = JackpotPoolSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def current_pools(self, request):
        """Pools actuels"""
        pools = self.queryset.all()
        serializer = self.get_serializer(pools, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def sync_pools(self, request):
        """Synchronise les pools avec Solana (admin seulement)"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            # Synchroniser l'état de la loterie
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            result = loop.run_until_complete(
                solana_service.sync_lottery_state()
            )
            
            loop.close()
            
            if result:
                return Response({
                    'success': 'Pools synchronisés',
                    'data': result
                })
            else:
                return Response(
                    {'error': 'Erreur lors de la synchronisation'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        except Exception as e:
            logger.error(f"Error syncing pools: {e}")
            return Response(
                {'error': 'Erreur lors de la synchronisation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class DashboardViewSet(viewsets.ViewSet):
    """ViewSet pour le tableau de bord"""
    permission_classes = [permissions.IsAuthenticated]
    
    def list(self, request):
        """Données du tableau de bord"""
        # Cache pour 30 secondes
        cache_key = 'dashboard_data'
        data = cache.get(cache_key)
        
        if not data:
            # Pools de jackpot actuels
            current_jackpots = JackpotPool.objects.all()
            
            # 3 derniers gagnants
            recent_winners = Winner.objects.filter(
                payout_status='completed'
            ).order_by('-created_at')[:3]
            
            # Transactions récentes
            recent_transactions = Transaction.objects.order_by('-block_time')[:10]
            
            data = {
                'current_jackpots': current_jackpots,
                'recent_winners': recent_winners,
                'recent_transactions': recent_transactions
            }
            
            cache.set(cache_key, data, 30)  # Cache 30 secondes
        
        serializer = DashboardSerializer(data)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def trigger_sync(self, request):
        """Déclenche une synchronisation complète"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            # Déclencher les tâches de synchronisation
            sync_lottery_state.delay()
            sync_participant_holdings.delay()
            
            # Log de l'action
            AuditLog.objects.create(
                action_type='system_sync',
                description='Synchronisation complète déclenchée',
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            
            return Response({'success': 'Synchronisation déclenchée'})
        except Exception as e:
            logger.error(f"Error triggering sync: {e}")
            return Response(
                {'error': 'Erreur lors du déclenchement'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def system_status(self, request):
        """Statut du système"""
        try:
            # Vérifier la connexion Solana
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            solana_status = loop.run_until_complete(
                self._check_solana_connection()
            )
            
            loop.close()
            
            # Vérifier les tâches Celery
            from celery import current_app
            celery_status = current_app.control.inspect().active()
            
            # Statistiques rapides
            pending_lotteries = Lottery.objects.filter(status='pending').count()
            pending_payouts = Winner.objects.filter(payout_status='pending').count()
            active_participants = TokenHolding.objects.filter(is_eligible=True).count()
            
            status_data = {
                'solana_connected': solana_status,
                'celery_active': bool(celery_status),
                'pending_lotteries': pending_lotteries,
                'pending_payouts': pending_payouts,
                'active_participants': active_participants,
                'timestamp': timezone.now()
            }
            
            return Response(status_data)
            
        except Exception as e:
            logger.error(f"Error checking system status: {e}")
            return Response(
                {'error': 'Erreur lors de la vérification du statut'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    async def _check_solana_connection(self):
        """Vérifie la connexion Solana"""
        try:
            connection = await solana_service.get_connection()
            result = await connection.get_health()
            await connection.close()
            return result == "ok"
        except:
            return False

class StatsViewSet(viewsets.ViewSet):
    """ViewSet pour les statistiques"""
    permission_classes = [permissions.IsAuthenticated]
    
    def list(self, request):
        """Statistiques générales"""
        cache_key = 'stats_data'
        data = cache.get(cache_key)
        
        if not data:
            # Statistiques des tirages
            total_lotteries = Lottery.objects.filter(status='completed').count()
            
            # Total des gains distribués
            total_winnings = Winner.objects.filter(
                payout_status='completed'
            ).aggregate(
                total=Sum('winning_amount_sol')
            )['total'] or 0
            
            # Jackpot moyen
            avg_jackpot = Lottery.objects.filter(
                status='completed'
            ).aggregate(
                avg=Avg('jackpot_amount_sol')
            )['avg'] or 0
            
            # Plus gros gain
            biggest_win = Winner.objects.filter(
                payout_status='completed'
            ).order_by('-winning_amount_sol').first()
            
            biggest_win_data = None
            if biggest_win:
                biggest_win_data = {
                    'amount': str(biggest_win.winning_amount_sol),
                    'wallet': f"{biggest_win.wallet_address[:6]}...{biggest_win.wallet_address[-4:]}",
                    'date': biggest_win.created_at
                }
            
            # Activité récente
            recent_activity = []
            recent_lotteries = Lottery.objects.filter(
                status='completed'
            ).order_by('-executed_time')[:5]
            
            for lottery in recent_lotteries:
                try:
                    winner = lottery.winner
                    recent_activity.append({
                        'type': 'lottery_completed',
                        'lottery_type': lottery.lottery_type,
                        'winner': f"{winner.wallet_address[:6]}...{winner.wallet_address[-4:]}",
                        'amount': str(winner.winning_amount_sol),
                        'date': lottery.executed_time
                    })
                except Winner.DoesNotExist:
                    pass
            
            # Fréquence des tirages
            lottery_frequency = {
                'hourly': Lottery.objects.filter(
                    lottery_type='hourly',
                    status='completed'
                ).count(),
                'daily': Lottery.objects.filter(
                    lottery_type='daily',
                    status='completed'
                ).count()
            }
            
            # Statistiques par période
            from datetime import timedelta
            now = timezone.now()
            
            # Dernières 24h
            last_24h = now - timedelta(hours=24)
            stats_24h = {
                'lotteries': Lottery.objects.filter(
                    executed_time__gte=last_24h,
                    status='completed'
                ).count(),
                'winnings': Winner.objects.filter(
                    created_at__gte=last_24h,
                    payout_status='completed'
                ).aggregate(total=Sum('winning_amount_sol'))['total'] or 0,
                'transactions': Transaction.objects.filter(
                    block_time__gte=last_24h
                ).count()
            }
            
            # Derniers 7 jours
            last_7d = now - timedelta(days=7)
            stats_7d = {
                'lotteries': Lottery.objects.filter
              (
                    executed_time__gte=last_7d,
                    status='completed'
                ).count(),
                'winnings': Winner.objects.filter(
                    created_at__gte=last_7d,
                    payout_status='completed'
                ).aggregate(total=Sum('winning_amount_sol'))['total'] or 0,
                'transactions': Transaction.objects.filter(
                    block_time__gte=last_7d
                ).count()
            }
            
            # Derniers 30 jours
            last_30d = now - timedelta(days=30)
            stats_30d = {
                'lotteries': Lottery.objects.filter(
                    executed_time__gte=last_30d,
                    status='completed'
                ).count(),
                'winnings': Winner.objects.filter(
                    created_at__gte=last_30d,
                    payout_status='completed'
                ).aggregate(total=Sum('winning_amount_sol'))['total'] or 0,
                'transactions': Transaction.objects.filter(
                    block_time__gte=last_30d
                ).count()
            }
            
            data = {
                'total_lotteries': total_lotteries,
                'total_winnings_distributed': total_winnings,
                'average_jackpot': avg_jackpot,
                'biggest_win': biggest_win_data,
                'recent_activity': recent_activity,
                'lottery_frequency': lottery_frequency,
                'stats_24h': stats_24h,
                'stats_7d': stats_7d,
                'stats_30d': stats_30d
            }
            
            cache.set(cache_key, data, 300)  # Cache 5 minutes
        
        serializer = StatsSerializer(data)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def lottery_history(self, request):
        """Historique détaillé des tirages"""
        lottery_type = request.query_params.get('type', None)
        days = int(request.query_params.get('days', 30))
        
        queryset = Lottery.objects.filter(
            status='completed',
            executed_time__gte=timezone.now() - timedelta(days=days)
        )
        
        if lottery_type:
            queryset = queryset.filter(lottery_type=lottery_type)
        
        # Grouper par jour
        history = queryset.extra(
            select={'day': 'date(executed_time)'}
        ).values('day', 'lottery_type').annotate(
            count=Count('id'),
            total_jackpot=Sum('jackpot_amount_sol'),
            avg_jackpot=Avg('jackpot_amount_sol'),
            total_participants=Sum('total_participants'),
            total_tickets=Sum('total_tickets')
        ).order_by('day')
        
        return Response(list(history))
    
    @action(detail=False, methods=['get'])
    def participant_stats(self, request):
        """Statistiques des participants"""
        cache_key = 'participant_stats'
        stats = cache.get(cache_key)
        
        if not stats:
            # Distribution des tickets
            ticket_distribution = TokenHolding.objects.filter(
                is_eligible=True
            ).values('tickets_count').annotate(
                count=Count('id')
            ).order_by('tickets_count')
            
            # Top détenteurs
            top_holders = TokenHolding.objects.filter(
                is_eligible=True
            ).order_by('-tickets_count')[:10]
            
            # Statistiques générales
            total_participants = TokenHolding.objects.filter(is_eligible=True).count()
            total_tickets = TokenHolding.objects.filter(
                is_eligible=True
            ).aggregate(total=Sum('tickets_count'))['total'] or 0
            
            avg_tickets = total_tickets / total_participants if total_participants > 0 else 0
            
            # Répartition par tranche de tickets
            ticket_ranges = [
                ('1-10', TokenHolding.objects.filter(tickets_count__range=(1, 10)).count()),
                ('11-50', TokenHolding.objects.filter(tickets_count__range=(11, 50)).count()),
                ('51-100', TokenHolding.objects.filter(tickets_count__range=(51, 100)).count()),
                ('101-500', TokenHolding.objects.filter(tickets_count__range=(101, 500)).count()),
                ('500+', TokenHolding.objects.filter(tickets_count__gt=500).count()),
            ]
            
            stats = {
                'total_participants': total_participants,
                'total_tickets': total_tickets,
                'average_tickets': round(avg_tickets, 2),
                'ticket_distribution': list(ticket_distribution),
                'top_holders': [
                    {
                        'wallet': f"{h.wallet_address[:6]}...{h.wallet_address[-4:]}",
                        'tickets': h.tickets_count,
                        'balance': str(h.balance)
                    }
                    for h in top_holders
                ],
                'ticket_ranges': ticket_ranges
            }
            
            cache.set(cache_key, stats, 300)  # Cache 5 minutes
        
        return Response(stats)

class WalletInfoViewSet(viewsets.ViewSet):
    """ViewSet pour les informations de portefeuille"""
    permission_classes = [permissions.IsAuthenticated]
    
    def retrieve(self, request, pk=None):
        """Informations détaillées d'un portefeuille"""
        wallet_address = pk
        
        # Vérifier les permissions
        if (wallet_address != request.user.wallet_address and 
            not request.user.is_staff):
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Détention actuelle
        try:
            holding = TokenHolding.objects.get(wallet_address=wallet_address)
            current_balance = holding.balance
            tickets_count = holding.tickets_count
            is_eligible = holding.is_eligible
        except TokenHolding.DoesNotExist:
            current_balance = 0
            tickets_count = 0
            is_eligible = False
        
        # Total des gains
        total_winnings = Winner.objects.filter(
            wallet_address=wallet_address,
            payout_status='completed'
        ).aggregate(
            total=Sum('winning_amount_sol')
        )['total'] or 0
        
        # Historique des gains
        win_history = Winner.objects.filter(
            wallet_address=wallet_address
        ).order_by('-created_at')[:20]
        
        # Transactions récentes
        recent_transactions = Transaction.objects.filter(
            wallet_address=wallet_address
        ).order_by('-block_time')[:50]
        
        # Statistiques de participation
        total_participations = Lottery.objects.filter(
            status='completed'
        ).count()  # Approximation
        
        total_wins = Winner.objects.filter(
            wallet_address=wallet_address
        ).count()
        
        win_rate = (total_wins / total_participations * 100) if total_participations > 0 else 0
        
        participation_stats = {
            'total_participations': total_participations,
            'total_wins': total_wins,
            'win_rate': round(win_rate, 2),
            'average_win': str(total_winnings / total_wins) if total_wins > 0 else '0'
        }
        
        data = {
            'wallet_address': wallet_address,
            'current_balance': current_balance,
            'tickets_count': tickets_count,
            'is_eligible': is_eligible,
            'total_winnings': total_winnings,
            'win_history': win_history,
            'recent_transactions': recent_transactions,
            'participation_stats': participation_stats
        }
        
        serializer = WalletInfoSerializer(data)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def sync_wallet(self, request, pk=None):
        """Synchronise un wallet avec Solana"""
        wallet_address = pk
        
        # Vérifier les permissions
        if (wallet_address != request.user.wallet_address and 
            not request.user.is_staff):
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            result = loop.run_until_complete(
                solana_service.sync_participant(wallet_address)
            )
            
            loop.close()
            
            if result:
                # Log de l'action
                AuditLog.objects.create(
                    action_type='wallet_synced',
                    description=f'Wallet {wallet_address} synchronisé',
                    user=request.user,
                    wallet_address=wallet_address,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                
                return Response({
                    'success': 'Wallet synchronisé',
                    'data': {
                        'balance': str(result.balance),
                        'tickets_count': result.tickets_count,
                        'is_eligible': result.is_eligible
                    }
                })
            else:
                return Response(
                    {'error': 'Impossible de synchroniser ce wallet'},
                    status=status.HTTP_404_NOT_FOUND
                )
                
        except Exception as e:
            logger.error(f"Error syncing wallet {wallet_address}: {e}")
            return Response(
                {'error': 'Erreur lors de la synchronisation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class SystemConfigViewSet(viewsets.ModelViewSet):
    """ViewSet pour la configuration système"""
    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    permission_classes = [IsAdminOrReadOnly]
    
    @action(detail=False, methods=['get'])
    def public_config(self, request):
        """Configuration publique"""
        public_configs = self.queryset.filter(
            key__in=[
                'hourly_lottery_enabled',
                'daily_lottery_enabled',
                'min_ticket_requirement',
                'maintenance_mode',
                'max_tickets_per_wallet',
                'lottery_fee_percentage'
            ]
        )
        
        config_dict = {
            config.key: config.value 
            for config in public_configs
        }
        
        return Response(config_dict)
    
    @action(detail=False, methods=['post'])
    def update_config(self, request):
        """Met à jour la configuration (admin seulement)"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        key = request.data.get('key')
        value = request.data.get('value')
        description = request.data.get('description', '')
        
        if not key or value is None:
            return Response(
                {'error': 'Clé et valeur requises'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            config, created = SystemConfig.objects.update_or_create(
                key=key,
                defaults={
                    'value': str(value),
                    'description': description,
                    'is_active': True
                }
            )
            
            # Log de l'action
            AuditLog.objects.create(
                action_type='config_updated',
                description=f'Configuration {key} mise à jour: {value}',
                user=request.user,
                metadata={'key': key, 'value': str(value), 'created': created},
                ip_address=request.META.get('REMOTE_ADDR')
            )
            
            serializer = self.get_serializer(config)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Error updating config {key}: {e}")
            return Response(
                {'error': 'Erreur lors de la mise à jour'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def solana_config(self, request):
        """Configuration Solana"""
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission refusée'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        from django.conf import settings
        
        solana_config = {
            'program_id': getattr(settings, 'SOLANA_PROGRAM_ID', ''),
            'rpc_url': getattr(settings, 'SOLANA_RPC_URL', ''),
            'commitment': getattr(settings, 'SOLANA_COMMITMENT', 'confirmed'),
            'network': 'devnet' if 'devnet' in getattr(settings, 'SOLANA_RPC_URL', '') else 'mainnet'
        }
        
        return Response(solana_config)

# ViewSet pour les logs d'audit (bonus)
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les logs d'audit"""
    queryset = AuditLog.objects.all()
    permission_classes = [permissions.IsAdminUser]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = ['action_type', 'user', 'wallet_address']
    search_fields = ['description', 'wallet_address']
    ordering_fields = ['timestamp']
    ordering = ['-timestamp']
    
    def get_serializer_class(self):
        from .serializers import AuditLogSerializer
        return AuditLogSerializer
    
    @action(detail=False, methods=['get'])
    def recent_activity(self, request):
        """Activité récente du système"""
        recent_logs = self.queryset.order_by('-timestamp')[:50]
        
        serializer = self.get_serializer(recent_logs, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def user_activity(self, request):
        """Activité d'un utilisateur spécifique"""
        user_id = request.query_params.get('user_id')
        wallet_address = request.query_params.get('wallet_address')
        
        queryset = self.queryset
        
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        
        if wallet_address:
            queryset = queryset.filter(wallet_address=wallet_address)
        
        if not user_id and not wallet_address:
            return Response(
                {'error': 'user_id ou wallet_address requis'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logs = queryset.order_by('-timestamp')[:100]
        serializer = self.get_serializer(logs, many=True)
        return Response(serializer.data)

