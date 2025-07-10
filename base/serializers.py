# serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import models
from .models import (
    TokenHolding, Lottery, Winner, Transaction, 
    JackpotPool, SystemConfig, AuditLog, LotteryType
)
from decimal import Decimal

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    """Serializer pour les utilisateurs"""
    total_winnings = serializers.SerializerMethodField()
    participation_count = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id', 'username', 'wallet_address', 'email',
            'is_active_participant', 'created_at', 'total_winnings',
            'participation_count'
        ]
        read_only_fields = ['id', 'created_at', 'total_winnings', 'participation_count']
    
    def get_total_winnings(self, obj):
        """Calcule le total des gains de l'utilisateur"""
        if not obj.wallet_address:
            return "0.000000000"
        
        total = Winner.objects.filter(
            wallet_address=obj.wallet_address,
            payout_status='completed'
        ).aggregate(
            total=models.Sum('winning_amount_sol')
        )['total'] or Decimal('0')
        
        return str(total)
    
    def get_participation_count(self, obj):
        """Nombre de tirages auxquels l'utilisateur a participé"""
        if not obj.wallet_address:
            return 0
        
        # Compte les tirages où l'utilisateur avait des tickets
        return TokenHolding.objects.filter(
            wallet_address=obj.wallet_address,
            tickets_count__gt=0
        ).count()

class TokenHoldingSerializer(serializers.ModelSerializer):
    """Serializer pour les détentions de tokens"""
    balance_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = TokenHolding
        fields = [
            'wallet_address', 'balance', 'balance_formatted',
            'tickets_count', 'is_eligible', 'last_updated'
        ]
        read_only_fields = ['tickets_count', 'is_eligible', 'last_updated']
    
    def get_balance_formatted(self, obj):
        """Balance formatée avec des séparateurs"""
        return f"{obj.balance:,.2f}"

class WinnerSerializer(serializers.ModelSerializer):
    """Serializer pour les gagnants"""
    lottery_type = serializers.CharField(source='lottery.lottery_type', read_only=True)
    lottery_date = serializers.DateTimeField(source='lottery.executed_time', read_only=True)
    wallet_short = serializers.SerializerMethodField()
    winning_amount_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = Winner
        fields = [
            'id', 'wallet_address', 'wallet_short', 'winning_amount_sol',
            'winning_amount_formatted', 'winning_amount_usd', 'tickets_held',
            'payout_status', 'payout_time', 'payout_transaction_signature',
            'lottery_type', 'lottery_date', 'created_at'
        ]
        read_only_fields = [
            'wallet_short', 'winning_amount_formatted', 'lottery_type', 'lottery_date'
        ]
    
    def get_wallet_short(self, obj):
        """Adresse wallet raccourcie"""
        return f"{obj.wallet_address[:6]}...{obj.wallet_address[-4:]}"
    
    def get_winning_amount_formatted(self, obj):
        """Montant formaté"""
        return f"{obj.winning_amount_sol:,.6f} SOL"

class LotteryListSerializer(serializers.ModelSerializer):
    """Serializer pour la liste des tirages"""
    winner_info = serializers.SerializerMethodField()
    time_until_draw = serializers.SerializerMethodField()
    jackpot_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = Lottery
        fields = [
            'id', 'lottery_type', 'scheduled_time', 'executed_time',
            'status', 'jackpot_amount_sol', 'jackpot_formatted',
            'jackpot_amount_usd', 'total_participants', 'total_tickets',
            'transaction_signature', 'slot_number', 'winner_info',
            'time_until_draw'
        ]
    
    def get_winner_info(self, obj):
        """Informations du gagnant si disponible"""
        try:
            winner = obj.winner
            return {
                'wallet_address': f"{winner.wallet_address[:6]}...{winner.wallet_address[-4:]}",
                'winning_amount': str(winner.winning_amount_sol),
                'tickets_held': winner.tickets_held,
                'payout_status': winner.payout_status
            }
        except Winner.DoesNotExist:
            return None
    
    def get_time_until_draw(self, obj):
        """Temps restant avant le tirage en secondes"""
        if obj.status != 'pending':
            return 0
        
        from django.utils import timezone
        now = timezone.now()
        if obj.scheduled_time > now:
            return int((obj.scheduled_time - now).total_seconds())
        return 0
    
    def get_jackpot_formatted(self, obj):
        """Jackpot formaté"""
        return f"{obj.jackpot_amount_sol:,.6f} SOL"

class LotteryDetailSerializer(LotteryListSerializer):
    """Serializer détaillé pour un tirage"""
    participants_list = serializers.SerializerMethodField()
    blockchain_info = serializers.SerializerMethodField()
    
    class Meta(LotteryListSerializer.Meta):
        fields = LotteryListSerializer.Meta.fields + [
            'participants_list', 'blockchain_info', 'vrf_request_id',
            'random_seed', 'created_at', 'updated_at'
        ]
    
    def get_participants_list(self, obj):
        """Liste des participants (limitée pour performance)"""
        if obj.status == 'pending':
            # Pour un tirage en cours, on récupère les participants actuels
            holdings = TokenHolding.objects.filter(
                is_eligible=True,
                tickets_count__gt=0
            ).order_by('-tickets_count')[:50]  # Top 50
            
            return [
                {
                    'wallet_address': f"{h.wallet_address[:6]}...{h.wallet_address[-4:]}",
                    'tickets_count': h.tickets_count,
                    'balance': str(h.balance)
                }
                for h in holdings
            ]
        return []
    
    def get_blockchain_info(self, obj):
        """Informations blockchain"""
        return {
            'transaction_signature': obj.transaction_signature,
            'slot_number': obj.slot_number,
            'solscan_url': f"https://solscan.io/tx/{obj.transaction_signature}" if obj.transaction_signature else None
        }

class TransactionSerializer(serializers.ModelSerializer):
    """Serializer pour les transactions"""
    wallet_short = serializers.SerializerMethodField()
    amount_formatted = serializers.SerializerMethodField()
    solscan_url = serializers.SerializerMethodField()
    
    class Meta:
        model = Transaction
        fields = [
            'id', 'transaction_type', 'wallet_address', 'wallet_short',
            'ball_amount', 'sol_amount', 'amount_formatted', 'usd_amount',
            'signature', 'slot', 'block_time', 'hourly_jackpot_contribution',
            'daily_jackpot_contribution', 'solscan_url', 'created_at'
        ]
        read_only_fields = [
            'wallet_short', 'amount_formatted', 'solscan_url',
            'hourly_jackpot_contribution', 'daily_jackpot_contribution'
        ]
    
    def get_wallet_short(self, obj):
        return f"{obj.wallet_address[:6]}...{obj.wallet_address[-4:]}"
    
    def get_amount_formatted(self, obj):
        if obj.transaction_type in ['buy', 'sell']:
            return f"{obj.ball_amount:,.0f} $BALL"
        return f"{obj.sol_amount:,.6f} SOL"
    
    def get_solscan_url(self, obj):
        return f"https://solscan.io/tx/{obj.signature}"

class JackpotPoolSerializer(serializers.ModelSerializer):
    """Serializer pour les pools de jackpot"""
    amount_formatted = serializers.SerializerMethodField()
    lottery_type_display = serializers.CharField(source='get_lottery_type_display', read_only=True)
    next_draw_time = serializers.SerializerMethodField()
    
    class Meta:
        model = JackpotPool
        fields = [
            'lottery_type', 'lottery_type_display', 'current_amount_sol',
            'amount_formatted', 'current_amount_usd', 'total_contributions',
            'total_payouts', 'next_draw_time', 'last_updated'
        ]
        read_only_fields = [
            'lottery_type_display', 'amount_formatted', 'next_draw_time'
        ]
    
    def get_amount_formatted(self, obj):
        return f"{obj.current_amount_sol:,.6f} SOL"
    
    def get_next_draw_time(self, obj):
        """Prochain tirage pour ce type"""
        from django.utils import timezone
        
        next_lottery = Lottery.objects.filter(
            lottery_type=obj.lottery_type,
            status='pending',
            scheduled_time__gt=timezone.now()
        ).first()
        
        return next_lottery.scheduled_time if next_lottery else None

class DashboardSerializer(serializers.Serializer):
    """Serializer pour le tableau de bord"""
    current_jackpots = JackpotPoolSerializer(many=True, read_only=True)
    recent_winners = WinnerSerializer(many=True, read_only=True)
    next_draws = serializers.SerializerMethodField()
    total_participants = serializers.SerializerMethodField()
    total_tickets = serializers.SerializerMethodField()
    recent_transactions = TransactionSerializer(many=True, read_only=True)
    
    def get_next_draws(self, obj):
        """Prochains tirages"""
        from django.utils import timezone
        
        next_draws = Lottery.objects.filter(
            status='pending',
            scheduled_time__gt=timezone.now()
        ).order_by('scheduled_time')[:2]
        
        return LotteryListSerializer(next_draws, many=True).data
    
    def get_total_participants(self, obj):
        """Nombre total de participants actifs"""
        return TokenHolding.objects.filter(is_eligible=True).count()
    
    def get_total_tickets(self, obj):
        """Nombre total de tickets"""
        return TokenHolding.objects.filter(
            is_eligible=True
        ).aggregate(
            total=models.Sum('tickets_count')
        )['total'] or 0

class StatsSerializer(serializers.Serializer):
    """Serializer pour les statistiques"""
    total_lotteries = serializers.IntegerField()
    total_winnings_distributed = serializers.DecimalField(max_digits=15, decimal_places=9)
    average_jackpot = serializers.DecimalField(max_digits=15, decimal_places=9)
    biggest_win = serializers.DictField()
    recent_activity = serializers.ListField()
    lottery_frequency = serializers.DictField()

class WalletInfoSerializer(serializers.Serializer):
    """Serializer pour les informations d'un wallet"""
    wallet_address = serializers.CharField()
    current_balance = serializers.DecimalField(max_digits=20, decimal_places=8)
    tickets_count = serializers.IntegerField()
    is_eligible = serializers.BooleanField()
    total_winnings = serializers.DecimalField(max_digits=15, decimal_places=9)
    win_history = WinnerSerializer(many=True, read_only=True)
    recent_transactions = TransactionSerializer(many=True, read_only=True)
    participation_stats = serializers.DictField()

# Serializers pour les créations/mises à jour
class LotteryCreateSerializer(serializers.ModelSerializer):
    """Serializer pour créer un tirage"""
    class Meta:
        model = Lottery
        fields = [
            'lottery_type', 'scheduled_time', 'jackpot_amount_sol',
            'jackpot_amount_usd'
        ]
    
    def validate_scheduled_time(self, value):
        """Validation du temps de tirage"""
        from django.utils import timezone
        
        if value <= timezone.now():
            raise serializers.ValidationError(
                "Le tirage doit être programmé dans le futur"
            )
        
        # Vérifier qu'il n'y a pas déjà un tirage à ce moment
        if Lottery.objects.filter(
            lottery_type=self.initial_data.get('lottery_type'),
            scheduled_time=value,
            status='pending'
        ).exists():
            raise serializers.ValidationError(
                "Un tirage est déjà programmé à cette heure"
            )
        
        return value

class SystemConfigSerializer(serializers.ModelSerializer):
    """Serializer pour la configuration système"""
    class Meta:
        model = SystemConfig
        fields = '__all__'

class AuditLogSerializer(serializers.ModelSerializer):
    """Serializer pour les logs d'audit"""
    user_display = serializers.CharField(source='user.username', read_only=True)
    lottery_display = serializers.CharField(source='lottery.__str__', read_only=True)
    
    class Meta:
        model = AuditLog
        fields = [
            'id', 'action_type', 'description', 'user', 'user_display',
            'lottery', 'lottery_display', 'wallet_address', 'metadata',
            'ip_address', 'timestamp'
        ]
        read_only_fields = [
            'user_display', 'lottery_display', 'timestamp'
        ]