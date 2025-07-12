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
from django.db import transaction


# Imports Solana
from base.solana_service import solana_service
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
    """ViewSet pour la gestion des utilisateurs (sans authentification)"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]  # Suppression des permissions restrictives
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['username', 'wallet_address', 'email']
    ordering_fields = ['created_at', 'username']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Retourne tous les utilisateurs (plus de filtre par utilisateur connecté)"""
        return User.objects.all()
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """Informations d'un utilisateur par ID"""
        user_id = request.query_params.get('user_id')
        if not user_id:
            return Response({'error': 'user_id requis'}, status=status.HTTP_400_BAD_REQUEST)
        
        user = get_object_or_404(User, id=user_id)
        serializer = self.get_serializer(user)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def connect_wallet(self, request, pk=None):
        """Connecter un portefeuille à l'utilisateur"""
        user = self.get_object()
        wallet_address = request.data.get('wallet_address')
        
        if not wallet_address:
            return Response({'error': 'Adresse de portefeuille requise'}, status=status.HTTP_400_BAD_REQUEST)
        
        if User.objects.filter(wallet_address=wallet_address).exclude(id=user.id).exists():
            return Response({'error': 'Ce portefeuille est déjà connecté à un autre compte'}, status=status.HTTP_400_BAD_REQUEST)
        
        user.wallet_address = wallet_address
        user.save()
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            holding = loop.run_until_complete(solana_service.sync_participant(wallet_address))
            loop.close()
            if holding:
                logger.info(f"Wallet {wallet_address} synchronized with Solana")
        except Exception as e:
            logger.error(f"Error syncing wallet {wallet_address} with Solana: {e}")
        
        AuditLog.objects.create(
            action_type='wallet_connected',
            description=f'Portefeuille {wallet_address} connecté',
            user=user,
            wallet_address=wallet_address,
            ip_address=request.META.get('REMOTE_ADDR')
        )
        
        return Response({'success': 'Portefeuille connecté avec succès'})

class TokenHoldingViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les détentions de tokens (sans authentification)"""
    queryset = TokenHolding.objects.all()
    serializer_class = TokenHoldingSerializer
    permission_classes = [permissions.AllowAny]  # Suppression de IsAuthenticated
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['is_eligible']
    ordering_fields = ['balance', 'tickets_count', 'last_updated']
    ordering = ['-tickets_count']

    @action(detail=False, methods=['get'])
    def leaderboard(self, request):
        """Classement des plus gros détenteurs"""
        top_holders = self.queryset.filter(is_eligible=True).order_by('-tickets_count')[:100]
        serializer = self.get_serializer(top_holders, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def my_holdings(self, request):
        """Holdings via wallet_address fourni"""
        wallet_address = request.query_params.get('wallet_address')
        if not wallet_address:
            return Response({'error': 'wallet_address requis'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            holding = TokenHolding.objects.get(wallet_address=wallet_address)
            serializer = self.get_serializer(holding)
            return Response(serializer.data)
        except TokenHolding.DoesNotExist:
            return Response({
                'wallet_address': wallet_address,
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
            return Response({'error': 'Adresse de wallet requise'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(solana_service.sync_participant(wallet_address))
            loop.close()

            if result:
                serializer = self.get_serializer(result)
                
                # Log de l'action
                AuditLog.objects.create(
                    action_type='wallet_synced',
                    description=f'Wallet {wallet_address} synchronisé via API',
                    user=None,
                    wallet_address=wallet_address,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    metadata={
                        'balance': str(result.balance),
                        'tickets_count': result.tickets_count,
                        'is_eligible': result.is_eligible
                    }
                )
                
                return Response(serializer.data)
            else:
                return Response({'error': 'Impossible de synchroniser ce wallet'}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Error syncing wallet {wallet_address}: {e}")
            return Response({'error': 'Erreur lors de la synchronisation'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def sync_all(self, request):
        """Synchronise tous les participants avec gestion robuste de Celery"""
        try:
            # ✅ Vérification robuste de Celery
            celery_status = self._check_celery_status()
            
            if celery_status['available']:
                # Utiliser Celery
                from .tasks import sync_participant_holdings
                
                try:
                    task = sync_participant_holdings.delay()
                    
                    # Log de l'action
                    AuditLog.objects.create(
                        action_type='bulk_sync_triggered',
                        description='Synchronisation en masse déclenchée via API',
                        user=None,
                        ip_address=request.META.get('REMOTE_ADDR'),
                        metadata={'task_id': task.id, 'mode': 'celery'}
                    )
                    
                    return Response({
                        'success': 'Synchronisation déclenchée (asynchrone)',
                        'task_id': task.id,
                        'mode': 'celery',
                        'estimated_duration': '2-5 minutes',
                        'workers_active': celery_status['workers_count']
                    })
                    
                except Exception as celery_error:
                    logger.error(f"Celery task failed: {celery_error}")
                    # Fallback vers synchrone
                    return self._sync_participants_synchronously(request, f"Celery error: {celery_error}")
            else:
                # Fallback synchrone
                return self._sync_participants_synchronously(request, celery_status['reason'])
                
        except Exception as e:
            logger.error(f"Error in sync_all: {e}")
            return Response(
                {
                    'error': f'Erreur lors de la synchronisation: {str(e)}',
                    'suggestion': 'Vérifiez que Redis et Celery sont démarrés'
                }, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _check_celery_status(self):
        """Vérification complète du statut Celery"""
        try:
            from celery import current_app
            
            # Vérifier la connexion au broker
            inspect = current_app.control.inspect()
            
            # Timeout court pour éviter les blocages
            active_workers = inspect.active()
            
            if not active_workers:
                return {
                    'available': False,
                    'reason': 'Aucun worker Celery actif',
                    'workers_count': 0
                }
            
            workers_count = len(active_workers)
            
            # Vérifier que les workers répondent
            stats = inspect.stats()
            if not stats:
                return {
                    'available': False,
                    'reason': 'Workers Celery ne répondent pas',
                    'workers_count': 0
                }
            
            return {
                'available': True,
                'reason': 'Celery opérationnel',
                'workers_count': workers_count
            }
            
        except Exception as e:
            return {
                'available': False,
                'reason': f'Erreur Celery: {str(e)}',
                'workers_count': 0
            }

    def _sync_participants_synchronously(self, request, reason):
        """Synchronisation synchrone en fallback"""
        logger.warning(f"Fallback to synchronous sync: {reason}")
        
        from .solana_service import solana_service
        import asyncio
        from django.utils import timezone
        from datetime import timedelta
        
        try:
            # Limiter à 15 wallets pour éviter les timeouts
            stale_wallets = TokenHolding.objects.filter(
                is_eligible=True,
                last_updated__lt=timezone.now() - timedelta(minutes=30)
            ).order_by('-tickets_count')[:15]
            
            if not stale_wallets.exists():
                return Response({
                    'success': 'Aucun wallet à synchroniser',
                    'synced_count': 0,
                    'total_attempted': 0,
                    'mode': 'synchronous',
                    'reason': reason
                })
            
            synced_count = 0
            errors = []
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                for holding in stale_wallets:
                    try:
                        result = loop.run_until_complete(
                            solana_service.sync_participant(holding.wallet_address)
                        )
                        if result:
                            synced_count += 1
                            logger.info(f"Synced wallet {holding.wallet_address}: {result.tickets_count} tickets")
                    except Exception as e:
                        error_msg = f"{holding.wallet_address[:8]}: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"Failed to sync {holding.wallet_address}: {e}")
                        continue
            finally:
                loop.close()
            
            # Log de l'action
            AuditLog.objects.create(
                action_type='bulk_sync_completed',
                description=f'Synchronisation synchrone terminée: {synced_count}/{len(stale_wallets)}',
                user=None,
                ip_address=request.META.get('REMOTE_ADDR'),
                metadata={
                    'mode': 'synchronous',
                    'synced_count': synced_count,
                    'total_attempted': len(stale_wallets),
                    'errors_count': len(errors),
                    'reason': reason
                }
            )
            
            return Response({
                'success': f'Synchronisation terminée (synchrone)',
                'synced_count': synced_count,
                'total_attempted': len(stale_wallets),
                'success_rate': f"{(synced_count/len(stale_wallets)*100):.1f}%",
                'errors': errors[:3],  # Limiter les erreurs affichées
                'mode': 'synchronous',
                'reason': reason,
                'note': 'Synchronisation limitée à 15 wallets en mode synchrone'
            })
            
        except Exception as e:
            logger.error(f"Synchronous sync failed: {e}")
            return Response(
                {
                    'error': f'Erreur lors de la synchronisation synchrone: {str(e)}',
                    'mode': 'synchronous',
                    'reason': reason
                }, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )



class LotteryViewSet(viewsets.ModelViewSet):
    """ViewSet pour les tirages (sans authentification ni permissions admin)"""
    queryset = Lottery.objects.all()
    permission_classes = [permissions.AllowAny]  # Suppression d'IsAuthenticated
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
        """Plus de logique par action, accès libre"""
        return [permissions.AllowAny()]
    
    
    def perform_create(self, serializer):
        """Crée un tirage et le synchronise avec Solana"""
        lottery = serializer.save()
        
        try:
            success = solana_service.some_method_sync()
            
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
        recent_lotteries = self.queryset.filter(status='completed').order_by('-executed_time')[:20]
        serializer = self.get_serializer(recent_lotteries, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        """Exécuter un tirage (auth désactivée)"""
        lottery = self.get_object()
        
        if lottery.status != 'pending':
            return Response({'error': 'Ce tirage ne peut pas être exécuté'}, status=status.HTTP_400_BAD_REQUEST)
        
        eligible_participants = TokenHolding.objects.filter(is_eligible=True, tickets_count__gt=0)
        if not eligible_participants.exists():
            return Response({'error': 'Aucun participant éligible'}, status=status.HTTP_400_BAD_REQUEST)
        
        winner = self._select_winner(eligible_participants)
        
        try:
            success = solana_service.some_method_sync()
            
            
            if success:
                AuditLog.objects.create(
                    action_type='lottery_executed',
                    description=f'Tirage {lottery.id} exécuté manuellement',
                    user=None,
                    lottery=lottery,
                    wallet_address=winner.wallet_address,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                
                return Response({
                    'success': 'Tirage exécuté avec succès',
                    'winner': winner.wallet_address
                })
            else:
                return Response({'error': 'Erreur lors de l\'exécution sur la blockchain'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution du tirage {lottery.id}: {e}")
            return Response({'error': 'Erreur lors de l\'exécution'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def sync_with_solana(self, request, pk=None):
        """Synchronise un tirage avec Solana (libre accès)"""
        lottery = self.get_object()
        
        try:
            sync_lottery_state.delay()
            return Response({'success': 'Synchronisation déclenchée'})
        except Exception as e:
            logger.error(f"Error triggering sync: {e}")
            return Response({'error': 'Erreur lors de la synchronisation'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _select_winner(self, participants):
        """Sélectionne un gagnant basé sur les tickets"""
        import random
        weighted_participants = []
        for participant in participants:
            weighted_participants.extend([participant] * participant.tickets_count)
        
        return random.choice(weighted_participants) if weighted_participants else participants.first()


class WinnerViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les gagnants (sans authentification)"""
    queryset = Winner.objects.all()
    serializer_class = WinnerSerializer
    permission_classes = [permissions.AllowAny]  # Suppression de IsAuthenticated
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
        """Gains via wallet_address fourni"""
        wallet_address = request.query_params.get('wallet_address')
        if not wallet_address:
            return Response({'error': 'wallet_address requis'}, status=status.HTTP_400_BAD_REQUEST)
        
        my_wins = self.queryset.filter(wallet_address=wallet_address).order_by('-created_at')
        serializer = self.get_serializer(my_wins, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def pay_winner(self, request, pk=None):
        """Payer un gagnant (authentification supprimée)"""
        winner = self.get_object()
        
        if winner.payout_status != 'pending':
            return Response({'error': 'Ce gagnant a déjà été payé ou est en cours de paiement'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success = loop.run_until_complete(solana_service.pay_winner_on_chain(winner))
            loop.close()
            
            if success:
                AuditLog.objects.create(
                    action_type='payout_sent',
                    description=f'Gagnant {winner.wallet_address} payé manuellement',
                    user=None,
                    lottery=winner.lottery,
                    wallet_address=winner.wallet_address,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                return Response({'success': 'Paiement effectué avec succès'})
            else:
                return Response({'error': 'Erreur lors du paiement sur la blockchain'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except Exception as e:
            logger.error(f"Error paying winner {winner.id}: {e}")
            return Response({'error': 'Erreur lors du paiement'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class JackpotPoolViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les pools de jackpot (sans authentification)"""
    queryset = JackpotPool.objects.all().order_by('id')  # ✅ Ajout de l'ordre explicite
    serializer_class = JackpotPoolSerializer
    permission_classes = [permissions.AllowAny]  # Auth désactivée

    @action(detail=False, methods=['get'])
    def current_pools(self, request):
        """Pools actuels avec ordre déterministe"""
        pools = self.get_queryset()  # Utilise le queryset avec order_by
        serializer = self.get_serializer(pools, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def sync_pools(self, request):
        """Synchronise les pools avec Solana (sans restriction admin)"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(solana_service.sync_lottery_state())
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


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les transactions (sans authentification)"""
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer
    permission_classes = [permissions.AllowAny]  # Auth supprimée
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
        """Transactions d'un wallet transmis dans la requête"""
        wallet_address = request.query_params.get('wallet_address')
        if not wallet_address:
            return Response({'error': 'wallet_address requis'}, status=status.HTTP_400_BAD_REQUEST)
        
        my_txs = self.queryset.filter(wallet_address=wallet_address).order_by('-block_time')
        serializer = self.get_serializer(my_txs, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Statistiques des transactions"""
        cache_key = 'transaction_stats'
        stats = cache.get(cache_key)
        
        if not stats:
            volume_by_type = self.queryset.values('transaction_type').annotate(
                total_sol=Sum('sol_amount'),
                total_ball=Sum('ball_amount'),
                count=Count('id')
            )
            
            total_hourly_contributions = self.queryset.aggregate(
                total=Sum('hourly_jackpot_contribution')
            )['total'] or 0
            
            total_daily_contributions = self.queryset.aggregate(
                total=Sum('daily_jackpot_contribution')
            )['total'] or 0
            
            from django.utils import timezone
            from datetime import timedelta
            
            last_7_days = timezone.now() - timedelta(days=7)
            daily_activity = self.queryset.filter(
                block_time__gte=last_7_days
            ).extra(select={'day': 'date(block_time)'}).values('day').annotate(
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
            
            cache.set(cache_key, stats, 300)
        
        return Response(stats)


class StatsViewSet(viewsets.ViewSet):
    """ViewSet pour les statistiques (sans authentification)"""
    permission_classes = [permissions.AllowAny]  # Suppression de IsAuthenticated
    
    def list(self, request):
        """Statistiques générales"""
        cache_key = 'stats_data'
        data = cache.get(cache_key)
        
        if not data:
            total_lotteries = Lottery.objects.filter(status='completed').count()
            total_winnings = Winner.objects.filter(payout_status='completed').aggregate(total=Sum('winning_amount_sol'))['total'] or 0
            avg_jackpot = Lottery.objects.filter(status='completed').aggregate(avg=Avg('jackpot_amount_sol'))['avg'] or 0
            
            biggest_win = Winner.objects.filter(payout_status='completed').order_by('-winning_amount_sol').first()
            biggest_win_data = None
            if biggest_win:
                biggest_win_data = {
                    'amount': str(biggest_win.winning_amount_sol),
                    'wallet': f"{biggest_win.wallet_address[:6]}...{biggest_win.wallet_address[-4:]}",
                    'date': biggest_win.created_at
                }
            
            recent_activity = []
            recent_lotteries = Lottery.objects.filter(status='completed').order_by('-executed_time')[:5]
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
            
            lottery_frequency = {
                'hourly': Lottery.objects.filter(lottery_type='hourly', status='completed').count(),
                'daily': Lottery.objects.filter(lottery_type='daily', status='completed').count()
            }
            
            from datetime import timedelta
            now = timezone.now()
            last_24h = now - timedelta(hours=24)
            last_7d = now - timedelta(days=7)
            last_30d = now - timedelta(days=30)
            
            stats_24h = {
                'lotteries': Lottery.objects.filter(executed_time__gte=last_24h, status='completed').count(),
                'winnings': Winner.objects.filter(created_at__gte=last_24h, payout_status='completed').aggregate(total=Sum('winning_amount_sol'))['total'] or 0,
                'transactions': Transaction.objects.filter(block_time__gte=last_24h).count()

            }
            
            stats_7d = {
                'lotteries': Lottery.objects.filter(executed_time__gte=last_7d, status='completed').count(),
                'winnings': Winner.objects.filter(created_at__gte=last_7d, payout_status='completed').aggregate(total=Sum('winning_amount_sol'))['total'] or 0,
                'transactions': Transaction.objects.filter(block_time__gte=last_7d).count()
            }
            
            stats_30d = {
                'lotteries': Lottery.objects.filter(executed_time__gte=last_30d, status='completed').count(),
                'winnings': Winner.objects.filter(created_at__gte=last_30d, payout_status='completed').aggregate(total=Sum('winning_amount_sol'))['total'] or 0,
                'transactions': Transaction.objects.filter(block_time__gte=last_30d).count()
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
            
            cache.set(cache_key, data, 300)
        
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
        
        history = queryset.extra(select={'day': 'date(executed_time)'}).values('day', 'lottery_type').annotate(
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
            ticket_distribution = TokenHolding.objects.filter(is_eligible=True).values('tickets_count').annotate(count=Count('id')).order_by('tickets_count')
            top_holders = TokenHolding.objects.filter(is_eligible=True).order_by('-tickets_count')[:10]
            total_participants = TokenHolding.objects.filter(is_eligible=True).count()
            total_tickets = TokenHolding.objects.filter(is_eligible=True).aggregate(total=Sum('tickets_count'))['total'] or 0
            avg_tickets = total_tickets / total_participants if total_participants > 0 else 0
            
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
            
            cache.set(cache_key, stats, 300)
        
        return Response(stats)


class SystemConfigViewSet(viewsets.ModelViewSet):
    """ViewSet pour la configuration système (sans authentification ni restriction admin)"""
    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    permission_classes = [permissions.AllowAny]  # Suppression de IsAdminOrReadOnly
    
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
        
        config_dict = {config.key: config.value for config in public_configs}
        return Response(config_dict)
    
    @action(detail=False, methods=['post'])
    def update_config(self, request):
        """Met à jour la configuration (accès libre)"""
        key = request.data.get('key')
        value = request.data.get('value')
        description = request.data.get('description', '')
        
        if not key or value is None:
            return Response({'error': 'Clé et valeur requises'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            config, created = SystemConfig.objects.update_or_create(
                key=key,
                defaults={
                    'value': str(value),
                    'description': description,
                    'is_active': True
                }
            )
            
            AuditLog.objects.create(
                action_type='config_updated',
                description=f'Configuration {key} mise à jour: {value}',
                user=None,
                metadata={'key': key, 'value': str(value), 'created': created},
                ip_address=request.META.get('REMOTE_ADDR')
            )
            
            serializer = self.get_serializer(config)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Error updating config {key}: {e}")
            return Response({'error': 'Erreur lors de la mise à jour'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def solana_config(self, request):
        """Configuration Solana (accès libre)"""
        from django.conf import settings
        
        solana_config = {
            'program_id': getattr(settings, 'SOLANA_PROGRAM_ID', ''),
            'rpc_url': getattr(settings, 'SOLANA_RPC_URL', ''),
            'commitment': getattr(settings, 'SOLANA_COMMITMENT', 'confirmed'),
            'network': 'devnet' if 'devnet' in getattr(settings, 'SOLANA_RPC_URL', '') else 'mainnet'
        }
        
        return Response(solana_config)


from celery import shared_task
from django.utils import timezone
from decimal import Decimal
import asyncio
import logging

from base.models import Lottery, LotteryType, AuditLog, SystemConfig, TokenHolding
from base.solana_service import solana_service

logger = logging.getLogger(__name__)

def get_config_value(key, default=False):
    """Récupère la valeur booléenne ou numérique d'une configuration système"""
    try:
        config = SystemConfig.objects.get(key=key)
        value = config.value
        if value.lower() in ['true', '1', 'yes']:
            return True
        if value.lower() in ['false', '0', 'no']:
            return False
        return Decimal(value)
    except (SystemConfig.DoesNotExist, ValueError):
        return default

def run_async_task(coro):
    """Exécute une coroutine de manière sécurisée dans Celery"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@shared_task
def create_scheduled_lotteries():
    """Crée les tirages programmés selon les configurations dynamiques"""
    now = timezone.now()
    created_count = 0

    # 🔧 Lire les flags de configuration
    hourly_enabled = get_config_value('hourly_lottery_enabled', default=True)
    daily_enabled = get_config_value('daily_lottery_enabled', default=True)
    min_jackpot_sol = get_config_value('min_jackpot_threshold', default=Decimal('0.001'))

    # 🧠 Lire l'état blockchain
    lottery_state = run_async_task(solana_service.get_lottery_state())
    if not lottery_state:
        logger.error("PRODUCTION: Impossible de lire l'état de Solana, aucun tirage généré")
        return 0

    # 🕐 Tirage horaire
    if hourly_enabled:
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1)
        if not Lottery.objects.filter(
            lottery_type=LotteryType.HOURLY,
            scheduled_time=next_hour,
            status='pending'
        ).exists():
            jackpot_amount = Decimal(str(lottery_state['hourly_jackpot'])) / Decimal('1000000000')
            if jackpot_amount >= min_jackpot_sol:
                Lottery.objects.create(
                    lottery_type=LotteryType.HOURLY,
                    scheduled_time=next_hour,
                    jackpot_amount_sol=jackpot_amount,
                    total_participants=lottery_state.get('total_participants', 0),
                    status='pending'
                )
                created_count += 1
                logger.info(f"PRODUCTION: Tirage HOURLY créé pour {next_hour} avec {jackpot_amount} SOL")
            else:
                logger.warning(f"Tirage HOURLY non créé: jackpot ({jackpot_amount}) < minimum ({min_jackpot_sol})")

    # 📆 Tirage journalier
    if daily_enabled and now.hour == 11 and now.minute >= 45:
        next_day_noon = (now + timezone.timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        if not Lottery.objects.filter(
            lottery_type=LotteryType.DAILY,
            scheduled_time=next_day_noon,
            status='pending'
        ).exists():
            jackpot_amount = Decimal(str(lottery_state['daily_jackpot'])) / Decimal('1000000000')
            if jackpot_amount >= min_jackpot_sol:
                Lottery.objects.create(
                    lottery_type=LotteryType.DAILY,
                    scheduled_time=next_day_noon,
                    jackpot_amount_sol=jackpot_amount,
                    total_participants=lottery_state.get('total_participants', 0),
                    status='pending'
                )
                created_count += 1
                logger.info(f"PRODUCTION: Tirage DAILY créé pour {next_day_noon} avec {jackpot_amount} SOL")
            else:
                logger.warning(f"Tirage DAILY non créé: jackpot ({jackpot_amount}) < minimum ({min_jackpot_sol})")

    return created_count


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


from .models import (
    JackpotPool, Winner, Transaction, TokenHolding, 
    Lottery, AuditLog, LotteryType
)
from .serializers import (
    DashboardSerializer, WalletInfoSerializer, TokenHoldingSerializer,
    WinnerSerializer, LotterySerializer, JackpotPoolSerializer
)
from base.solana_service import solana_service

logger = logging.getLogger(__name__)


import asyncio
import logging
from decimal import Decimal
from django.core.cache import cache

import time

from .models import (
    JackpotPool, Winner, Transaction, TokenHolding, 
    Lottery, AuditLog, LotteryType
)
from .serializers import DashboardSerializer
from base.solana_service import solana_service

logger = logging.getLogger(__name__)

class ProductionDashboardViewSet(viewsets.ViewSet):
    
    """🔹 PRODUCTION: ViewSet pour le tableau de bord optimisé"""

    permission_classes = [permissions.AllowAny]

    def list(self, request):
        """🔹 PRODUCTION: Données du tableau de bord avec cache intelligent"""
        cache_key = 'dashboard_data_production'
        data = cache.get(cache_key)

        if not data:
            try:
                with transaction.atomic():
                    # 🔹 PRODUCTION: Requêtes optimisées
                    current_jackpots = JackpotPool.objects.select_related().all()
                    recent_winners = Winner.objects.select_related('lottery').filter(
                        payout_status='completed'
                    ).order_by('-created_at')[:5]
                    
                    recent_transactions = Transaction.objects.select_related().order_by(
                        '-block_time'
                    )[:10]
                    
                    current_lottery = Lottery.objects.filter(
                        status='pending'
                    ).order_by('scheduled_time').first()

                    # 🔹 PRODUCTION: Statistiques avec cache
                    stats_cache_key = 'dashboard_stats_production'
                    stats = cache.get(stats_cache_key)
                    
                    if not stats:
                        stats = {
                            'total_participants': TokenHolding.objects.filter(is_eligible=True).count(),
                            'total_draws': Lottery.objects.filter(status='completed').count(),
                            'total_winnings': Winner.objects.filter(
                                payout_status='completed'
                            ).aggregate(total=Sum('winning_amount_sol'))['total'] or Decimal('0'),
                            'active_tickets': TokenHolding.objects.aggregate(
                                total=Sum('tickets_count')
                            )['total'] or 0
                        }
                        cache.set(stats_cache_key, stats, 300)  # 5 minutes

                    data = {
                        'current_jackpots': current_jackpots,
                        'recent_winners': recent_winners,
                        'recent_transactions': recent_transactions,
                        'current_lottery': current_lottery,
                        'stats': stats,
                        'last_updated': int(time.time())
                    }

                    # 🔹 PRODUCTION: Cache pour 60 secondes
                    cache.set(cache_key, data, 60)
                    
            except Exception as e:
                logger.error(f"❌ PRODUCTION: Error fetching dashboard data: {e}")
                return Response(
                    {'error': 'Unable to fetch dashboard data'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        serializer = DashboardSerializer(data)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def trigger_sync(self, request):
        """🔹 PRODUCTION: Déclenche une synchronisation avec monitoring"""
        try:
            from .tasks import sync_lottery_state, sync_participant_holdings
            
            # 🔹 PRODUCTION: Vérifier les tâches en cours
            active_syncs = cache.get('active_syncs', [])
            if len(active_syncs) > 3:
                return Response(
                    {'error': 'Too many sync operations in progress'}, 
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )
            
            # 🔹 PRODUCTION: Lancer les tâches
            lottery_task = sync_lottery_state.delay()
            participant_task = sync_participant_holdings.delay()
            
            # 🔹 PRODUCTION: Tracker les tâches actives
            active_syncs.extend([lottery_task.id, participant_task.id])
            cache.set('active_syncs', active_syncs, 300)
            
            # 🔹 PRODUCTION: Log d'audit
            AuditLog.objects.create(
                action_type='system_sync_triggered',
                description='Synchronisation complète déclenchée via API',
                metadata={
                    'lottery_task_id': lottery_task.id,
                    'participant_task_id': participant_task.id,
                    'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                    'ip_address': request.META.get('REMOTE_ADDR', '')
                },
                ip_address=request.META.get('REMOTE_ADDR')
            )

            return Response({
                'success': 'Synchronisation déclenchée',
                'task_ids': {
                    'lottery_sync': lottery_task.id,
                    'participant_sync': participant_task.id
                }
            })

        except Exception as e:
            logger.error(f"❌ PRODUCTION: Error triggering sync: {e}")
            return Response(
                {'error': 'Erreur lors du déclenchement de la synchronisation'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def system_status(self, request):
        """🔹 PRODUCTION: Statut du système avec métriques détaillées"""
        try:
            async def check_system_health():
                try:
                    health_status = await asyncio.wait_for(
                        solana_service.get_health_status(),
                        timeout=10.0
                    )
                    return health_status
                except asyncio.TimeoutError:
                    return {'solana_rpc_healthy': False, 'error': 'Timeout'}
                except Exception as e:
                    return {'solana_rpc_healthy': False, 'error': str(e)}

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            lambda: asyncio.run(check_system_health())
                        )
                        solana_status = future.result(timeout=15)
                else:
                    solana_status = loop.run_until_complete(check_system_health())
            except RuntimeError:
                solana_status = asyncio.run(check_system_health())

            celery_active = False
            celery_workers = 0
            try:
                from celery import current_app
                inspect = current_app.control.inspect()
                active_tasks = inspect.active()
                if active_tasks:
                    celery_active = True
                    celery_workers = len(active_tasks.keys())
            except Exception as e:
                logger.warning(f"Celery check failed: {e}")

            db_metrics = {
                'pending_lotteries': Lottery.objects.filter(status='pending').count(),
                'pending_payouts': Winner.objects.filter(payout_status='pending').count(),
                'active_participants': TokenHolding.objects.filter(is_eligible=True).count(),
                'total_transactions': Transaction.objects.count(),
                'last_transaction': Transaction.objects.order_by('-created_at').first()
            }

            status_data = {
                'solana': solana_status,
                'celery': {
                    'active': celery_active,
                    'workers': celery_workers
                },
                'database': db_metrics,
                'cache': {
                    'active': bool(cache.get('test_key') is None),
                    'keys_count': len(cache._cache.keys()) if hasattr(cache, '_cache') else 'unknown'
                },
                'system': {
                    'timestamp': timezone.now(),
                    'uptime': int(time.time()),
                    'version': '1.0.0'
                }
            }

            return Response(status_data)

        except Exception as e:
            logger.error(f"❌ PRODUCTION: Error checking system status: {e}")
            return Response(
                {'error': 'Erreur lors de la vérification du statut système'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def lottery_state(self, request):
        
        """🔹 PRODUCTION: État de la loterie avec fallback robuste"""
        try:
            cache_key = 'lottery_state_api_response'
            cached_response = cache.get(cache_key)
            
            if cached_response:
                logger.info("📦 PRODUCTION: Returning cached lottery state")
                return Response(cached_response)

            import concurrent.futures
            import threading

            def get_state():
                try:
                    return asyncio.run(solana_service.get_lottery_state())
                except Exception as e:
                    logger.error(f"Error in thread: {e}")
                    return None

            try:
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(get_state)
                    state = future.result(timeout=15)
            except concurrent.futures.TimeoutError:
                logger.error("⏰ PRODUCTION: Lottery state fetch timeout")
                state = None
            except Exception as e:
                logger.error(f"❌ PRODUCTION: Error in executor: {e}")
                state = None

            if not state:
                state = {
                    'hourly_jackpot': 0,
                    'daily_jackpot': 0,
                    'total_participants': 0,
                    'is_active': False,
                    'error': 'Unable to fetch from blockchain'
                }

            response_data = {
                'success': True,
                'data': state,
                'cached': False,
                'timestamp': int(time.time())
            }

            cache.set(cache_key, response_data, 45)

            return Response(response_data)

        except Exception as e:
            logger.error(f"❌ PRODUCTION: Critical error in lottery_state: {e}")
            
            error_response = {
                'success': False,
                'data': {
                    'hourly_jackpot': 0,
                    'daily_jackpot': 0,
                    'total_participants': 0,
                    'is_active': False,
                    'error': 'Service temporairement indisponible'
                },
                'error': 'Service temporairement indisponible',
                'timestamp': int(time.time())
            }
            
            return Response(error_response, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """🔹 PRODUCTION: Statistiques avancées avec cache et optimisations"""
        try:
            cache_key = 'dashboard_stats_advanced'
            stats = cache.get(cache_key)
            
            if not stats:
                with transaction.atomic():
                    total_participants = TokenHolding.objects.filter(is_eligible=True).count()
                    total_tickets = TokenHolding.objects.filter(
                        is_eligible=True
                    ).aggregate(total=Sum('tickets_count'))['total'] or 0
                    
                    total_draws = Lottery.objects.filter(status='completed').count()
                    total_winnings = Winner.objects.filter(
                        payout_status='completed'
                    ).aggregate(total=Sum('winning_amount_sol'))['total'] or Decimal('0')

                    burned_tokens_cache_key = 'burned_tokens_calculation'
                    burned_tokens_amount = cache.get(burned_tokens_cache_key)
                    
                    if burned_tokens_amount is None:
                        burned_tokens_amount = Transaction.objects.filter(
                            transaction_type='burn'
                        ).aggregate(total=Sum('ball_amount'))['total'] or Decimal('0')
                        cache.set(burned_tokens_cache_key, burned_tokens_amount, 1800)

                    token_to_ticket_ratio = Decimal('10000')
                    burned_tickets = int(burned_tokens_amount / token_to_ticket_ratio) if burned_tokens_amount > 0 else 0

                    hourly_stats_cache_key = 'hourly_stats_production'
                    hourly_stats = cache.get(hourly_stats_cache_key)
                    
                    if not hourly_stats:
                        hourly_stats = {
                            'total_draws': Lottery.objects.filter(
                                lottery_type=LotteryType.HOURLY, 
                                status='completed'
                            ).count(),
                            'total_winnings': Winner.objects.filter(
                                lottery__lottery_type=LotteryType.HOURLY, 
                                payout_status='completed'
                            ).aggregate(total=Sum('winning_amount_sol'))['total'] or Decimal('0')
                        }
                        cache.set(hourly_stats_cache_key, hourly_stats, 600)

                    daily_stats_cache_key = 'daily_stats_production'
                    daily_stats = cache.get(daily_stats_cache_key)
                    
                    if not daily_stats:
                        daily_stats = {
                            'total_draws': Lottery.objects.filter(
                                lottery_type=LotteryType.DAILY, 
                                status='completed'
                            ).count(),
                            'total_winnings': Winner.objects.filter(
                                lottery__lottery_type=LotteryType.DAILY, 
                                payout_status='completed'
                            ).aggregate(total=Sum('winning_amount_sol'))['total'] or Decimal('0')
                        }
                        cache.set(daily_stats_cache_key, daily_stats, 600)

                    stats = {
                        'total_participants': total_participants,
                        'total_tickets': total_tickets,
                        'total_draws': total_draws,
                        'total_winnings': str(total_winnings),
                        'burned_tokens': str(burned_tokens_amount),
                        'burned_tickets': burned_tickets,
                        'active_lotteries': Lottery.objects.filter(status='pending').count(),
                        'hourly_stats': {
                            'total_draws': hourly_stats['total_draws'],
                            'total_winnings': str(hourly_stats['total_winnings'])
                        },
                        'daily_stats': {
                            'total_draws': daily_stats['total_draws'],
                            'total_winnings': str(daily_stats['total_winnings'])
                        },
                        'performance_metrics': {
                            'avg_participants_per_draw': round(total_participants / max(total_draws, 1), 2),
                            'avg_winning_per_draw': str(total_winnings / max(total_draws, 1)) if total_draws > 0 else '0',
                            'ticket_burn_ratio': round(float(burned_tickets / max(total_tickets, 1)) * 100, 2) if total_tickets > 0 else 0
                        },
                        'last_updated': int(time.time())
                    }

                    cache.set(cache_key, stats, 300)

            return Response(stats)

        except Exception as e:
            logger.error(f"❌ PRODUCTION: Error calculating stats: {e}")
            fallback_stats = {
                'total_participants': 0,
                'total_tickets': 0,
                'total_draws': 0,
                'total_winnings': '0',
                'burned_tokens': '0',
                'burned_tickets': 0,
                'active_lotteries': 0,
                'hourly_stats': {'total_draws': 0, 'total_winnings': '0'},
                'daily_stats': {'total_draws': 0, 'total_winnings': '0'},
                'error': 'Unable to calculate statistics',
                'last_updated': int(time.time())
            }
            return Response(fallback_stats)

    async def _check_solana_connection_production(self):
        """🔹 PRODUCTION: Vérification de connexion Solana optimisée"""
        try:
            health_status = await asyncio.wait_for(
                solana_service.get_health_status(),
                timeout=8.0
            )
            return health_status.get('solana_rpc_healthy', False)
        except Exception as e:
            logger.error(f"❌ PRODUCTION: Solana connection check failed: {e}")
            return False

# 🔹 PRODUCTION: Remplacer l'ancien ViewSet
DashboardViewSet = ProductionDashboardViewSet


class WalletInfoViewSet(viewsets.ViewSet):
    """ViewSet pour les informations de portefeuille (sans authentification)"""
    permission_classes = [permissions.AllowAny]

    def retrieve(self, request, pk=None):
        wallet_address = pk

        try:
            holding = TokenHolding.objects.get(wallet_address=wallet_address)
            current_balance = holding.balance
            tickets_count = holding.tickets_count
            is_eligible = holding.is_eligible
            last_updated = holding.last_updated
        except TokenHolding.DoesNotExist:
            current_balance = Decimal('0')
            tickets_count = 0
            is_eligible = False
            last_updated = None

        total_winnings = Winner.objects.filter(
            wallet_address=wallet_address,
            payout_status='completed'
        ).aggregate(total=Sum('winning_amount_sol'))['total'] or Decimal('0')

        win_history = Winner.objects.filter(
            wallet_address=wallet_address
        ).select_related('lottery').order_by('-created_at')[:20]

        recent_transactions = Transaction.objects.filter(
            wallet_address=wallet_address
        ).order_by('-block_time')[:50]

        total_participations = Lottery.objects.filter(status='completed').count()
        total_wins = Winner.objects.filter(wallet_address=wallet_address).count()
        win_rate = (total_wins / total_participations * 100) if total_participations > 0 else 0

        participation_stats = {
            'total_participations': total_participations,
            'total_wins': total_wins,
            'win_rate': round(win_rate, 2),
            'average_win': str(total_winnings / total_wins) if total_wins > 0 else '0'
        }

        rank_by_balance = TokenHolding.objects.filter(
            balance__gt=current_balance, is_eligible=True
        ).count() + 1

        rank_by_tickets = TokenHolding.objects.filter(
            tickets_count__gt=tickets_count, is_eligible=True
        ).count() + 1

        roi_estimation = round(float(total_winnings) * 0.10, 4)
        was_active = TokenHolding.objects.filter(
            wallet_address=wallet_address,
            last_updated__lt=timezone.now()
        ).exists()

        data = {
            'wallet_address': wallet_address,
            'current_balance': str(current_balance),
            'tickets_count': tickets_count,
            'is_eligible': is_eligible,
            'last_updated': last_updated,
            'was_active': was_active,
            'total_winnings': str(total_winnings),
            'estimated_roi_percentage': "10",
            'estimated_roi_amount': str(roi_estimation),
            'win_history': WinnerSerializer(win_history, many=True).data,
            'recent_transactions': recent_transactions,
            'participation_stats': participation_stats,
            'rankings': {
                'balance_rank': rank_by_balance,
                'tickets_rank': rank_by_tickets
            }
        }

        return Response(data)

    @action(detail=True, methods=['post'])
    def sync_wallet(self, request, pk=None):
        wallet_address = pk

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(solana_service.sync_participant(wallet_address))
            loop.close()

            if result:
                AuditLog.objects.create(
                    action_type='wallet_synced',
                    description=f'Wallet {wallet_address} synchronisé',
                    user=None,
                    wallet_address=wallet_address,
                    ip_address=request.META.get('REMOTE_ADDR')
                )

                return Response({
                    'success': 'Wallet synchronisé',
                    'data': {
                        'balance': str(result.balance),
                        'tickets_count': result.tickets_count,
                        'is_eligible': result.is_eligible,
                        'last_updated': result.last_updated
                    }
                })

            return Response({'error': 'Impossible de synchroniser ce wallet'}, status=404)

        except Exception as e:
            logger.error(f"Error syncing wallet {wallet_address}: {e}")
            return Response({'error': 'Erreur lors de la synchronisation'}, status=500)

    @action(detail=False, methods=['get'])
    def leaderboard(self, request):
        try:
            board_type = request.query_params.get('type', 'balance')
            limit = int(request.query_params.get('limit', 10))

            base_queryset = TokenHolding.objects.filter(is_eligible=True)

            if board_type == 'tickets':
                queryset = base_queryset.order_by('-tickets_count')[:limit]
            elif board_type == 'winnings':
                queryset = base_queryset.annotate(
                    total_winnings_calc=Sum(
                        'winner__winning_amount_sol',
                        filter=Q(winner__payout_status='completed')
                    )
                ).order_by('-total_winnings_calc')[:limit]
            else:
                queryset = base_queryset.order_by('-balance')[:limit]

            serializer = TokenHoldingSerializer(queryset, many=True)
            return Response({
                'count': queryset.count(),
                'results': serializer.data,
                'type': board_type
            })

        except Exception as e:
            logger.error(f"Error fetching leaderboard: {e}")
            return Response({'error': str(e)}, status=500)

    @action(detail=False, methods=['get'])
    def search(self, request):
        query = request.query_params.get('q', '').strip()

        if not query or len(query) < 4:
            return Response({'error': 'Query must be at least 4 characters long'}, status=400)

        try:
            wallets = TokenHolding.objects.filter(
                wallet_address__icontains=query,
                is_eligible=True
            ).order_by('-balance')[:20]

            serializer = TokenHoldingSerializer(wallets, many=True)
            return Response({
                'count': wallets.count(),
                'results': serializer.data,
                'query': query
            })

        except Exception as e:
            logger.error(f"Error searching wallets: {e}")
            return Response({'error': str(e)}, status=500)

    @action(detail=True, methods=['get'])
    def participation_history(self, request, pk=None):
        wallet_address = pk

        try:
            wins = Winner.objects.filter(wallet_address=wallet_address).select_related('lottery').order_by('-created_at')
            recent_lotteries = Lottery.objects.filter(status='completed').order_by('-executed_time')[:50]

            participation_data = []
            win_lottery_ids = [w.lottery.id for w in wins]

            for win in wins:
                participation_data.append({
                    'lottery_id': win.lottery.id,
                    'lottery_type': win.lottery.lottery_type,
                    'date': win.created_at,
                    'status': 'won',
                    'amount': str(win.winning_amount_sol),
                    'tickets_held': win.tickets_held,
                    'total_participants': win.lottery.total_participants,
                    'total_tickets': win.lottery.total_tickets
                })

            for lottery in recent_lotteries:
                if lottery.id not in win_lottery_ids:
                    was_active = TokenHolding.objects.filter(
                        wallet_address=wallet_address,
                        last_updated__lte=lottery.executed_time
                    ).exists()

                    if was_active:
                        participation_data.append({
                            'lottery_id': lottery.id,
                            'lottery_type': lottery.lottery_type,
                            'date': lottery.executed_time,
                            'status': 'participated',
                            'amount': '0',
                            'tickets_held': 0,
                            'total_participants': lottery.total_participants,
                            'total_tickets': lottery.total_tickets
                        })

            participation_data.sort(key=lambda x: x['date'], reverse=True)

            return Response({
                'wallet_address': wallet_address,
                'participation_history': participation_data[:100],
                'total_participations': len(participation_data),
                'total_wins': len(wins)
            })

        except Exception as e:
            logger.error(f"Error fetching participation history for {wallet_address}: {e}")
            return Response({'error': str(e)}, status=500)

    @action(detail=False, methods=['get'])
    def bulk_sync(self, request):
        try:
            from datetime import timedelta
            stale_wallets = TokenHolding.objects.filter(
                is_eligible=True,
                last_updated__lt=timezone.now() - timedelta(hours=1)
            ).order_by('last_updated')[:50]

            from .tasks import bulk_sync_wallets
            task = bulk_sync_wallets.delay([w.wallet_address for w in stale_wallets])

            AuditLog.objects.create(
                action_type='bulk_sync',
                description=f'Synchronisation en masse de {len(stale_wallets)} wallets',
                user=None,
                ip_address=request.META.get('REMOTE_ADDR')
            )

            return Response({
                'success': f'Synchronisation de {len(stale_wallets)} wallets déclenchée',
                'task_id': task.id,
                'wallets_count': len(stale_wallets)
            })

        except Exception as e:
            logger.error(f"Error triggering bulk sync: {e}")
            return Response({'error': str(e)}, status=500)



