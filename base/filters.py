import django_filters
from django_filters import rest_framework as filters
from .models import Lottery, Transaction, Winner, LotteryType

class LotteryFilter(filters.FilterSet):
    """Filtres pour les tirages"""
    lottery_type = filters.ChoiceFilter(choices=LotteryType.choices)
    status = filters.CharFilter()
    date_from = filters.DateTimeFilter(field_name='scheduled_time', lookup_expr='gte')
    date_to = filters.DateTimeFilter(field_name='scheduled_time', lookup_expr='lte')
    jackpot_min = filters.NumberFilter(field_name='jackpot_amount_sol', lookup_expr='gte')
    jackpot_max = filters.NumberFilter(field_name='jackpot_amount_sol', lookup_expr='lte')
    has_winner = filters.BooleanFilter(method='filter_has_winner')
    
    class Meta:
        model = Lottery
        fields = [
            'lottery_type', 'status', 'date_from', 'date_to',
            'jackpot_min', 'jackpot_max', 'has_winner'
        ]
    
    def filter_has_winner(self, queryset, name, value):
        if value:
            return queryset.filter(winner__isnull=False)
        return queryset.filter(winner__isnull=True)

class TransactionFilter(filters.FilterSet):
    """Filtres pour les transactions"""
    transaction_type = filters.CharFilter()
    wallet_address = filters.CharFilter()
    date_from = filters.DateTimeFilter(field_name='block_time', lookup_expr='gte')
    date_to = filters.DateTimeFilter(field_name='block_time', lookup_expr='lte')
    amount_min = filters.NumberFilter(field_name='sol_amount', lookup_expr='gte')
    amount_max = filters.NumberFilter(field_name='sol_amount', lookup_expr='lte')
    
    class Meta:
        model = Transaction
        fields = [
            'transaction_type', 'wallet_address', 'date_from',
            'date_to', 'amount_min', 'amount_max'
        ]

class WinnerFilter(filters.FilterSet):
    """Filtres pour les gagnants"""
    lottery_type = filters.CharFilter(field_name='lottery__lottery_type')
    wallet_address = filters.CharFilter()
    date_from = filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    date_to = filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')
    amount_min = filters.NumberFilter(field_name='winning_amount_sol', lookup_expr='gte')
    amount_max = filters.NumberFilter(field_name='winning_amount_sol', lookup_expr='lte')
    payout_status = filters.CharFilter()
    
    class Meta:
        model = Winner
        fields = [
            'lottery_type', 'wallet_address', 'date_from',
            'date_to', 'amount_min', 'amount_max', 'payout_status'
        ]
