from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.db.models import Sum, Count
from django.utils import timezone
from .models import (
    User, TokenHolding, Lottery, Winner, Transaction, 
    JackpotPool, SystemConfig, AuditLog
)

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Administration des utilisateurs personnalisés"""
    list_display = ('username', 'email', 'wallet_address_short', 'is_active_participant', 'date_joined', 'is_staff')
    list_filter = ('is_active_participant', 'is_staff', 'is_superuser', 'date_joined')
    search_fields = ('username', 'email', 'wallet_address')
    ordering = ('-date_joined',)
    
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Blockchain Info', {
            'fields': ('wallet_address', 'is_active_participant'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at', 'date_joined', 'last_login')
    
    def wallet_address_short(self, obj):
        if obj.wallet_address:
            return f"{obj.wallet_address[:8]}...{obj.wallet_address[-4:]}"
        return "No wallet"
    wallet_address_short.short_description = "Wallet"
    wallet_address_short.admin_order_field = 'wallet_address'

@admin.register(TokenHolding)
class TokenHoldingAdmin(admin.ModelAdmin):
    """Administration des détentions de tokens"""
    list_display = ('wallet_short', 'balance_formatted', 'tickets_count', 'is_eligible', 'last_updated')
    list_filter = ('is_eligible', 'last_updated')
    search_fields = ('wallet_address',)
    ordering = ('-tickets_count', '-balance')
    readonly_fields = ('tickets_count', 'last_updated')
    
    fieldsets = (
        ('Wallet Info', {
            'fields': ('wallet_address',)
        }),
        ('Token Holdings', {
            'fields': ('balance', 'tickets_count', 'is_eligible'),
            'classes': ('wide',)
        }),
        ('Metadata', {
            'fields': ('last_updated',),
            'classes': ('collapse',)
        }),
    )
    
    def wallet_short(self, obj):
        return f"{obj.wallet_address[:8]}...{obj.wallet_address[-4:]}"
    wallet_short.short_description = "Wallet"
    wallet_short.admin_order_field = 'wallet_address'
    
    def balance_formatted(self, obj):
        return f"{obj.balance:,.2f} $BALL"
    balance_formatted.short_description = "Balance"
    balance_formatted.admin_order_field = 'balance'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related()

@admin.register(Lottery)
class LotteryAdmin(admin.ModelAdmin):
    """Administration des tirages de loterie"""
    list_display = ('lottery_type', 'scheduled_time', 'status_badge', 'jackpot_sol', 'total_participants', 'winner_link')
    list_filter = ('lottery_type', 'status', 'scheduled_time')
    search_fields = ('transaction_signature', 'vrf_request_id')
    ordering = ('-scheduled_time',)
    readonly_fields = ('id', 'created_at', 'updated_at', 'executed_time')
    
    fieldsets = (
        ('Lottery Info', {
            'fields': ('lottery_type', 'scheduled_time', 'executed_time', 'status'),
            'classes': ('wide',)
        }),
        ('Jackpot', {
            'fields': ('jackpot_amount_sol', 'jackpot_amount_usd'),
            'classes': ('wide',)
        }),
        ('Participants', {
            'fields': ('total_participants', 'total_tickets'),
        }),
        ('Blockchain', {
            'fields': ('transaction_signature', 'slot_number'),
            'classes': ('collapse',)
        }),
        ('VRF', {
            'fields': ('vrf_request_id', 'random_seed'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def status_badge(self, obj):
        colors = {
            'pending': '#ffc107',
            'processing': '#17a2b8',
            'completed': '#28a745',
            'failed': '#dc3545'
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = "Status"
    status_badge.admin_order_field = 'status'
    
    def jackpot_sol(self, obj):
        return f"{obj.jackpot_amount_sol:.4f} SOL"
    jackpot_sol.short_description = "Jackpot"
    jackpot_sol.admin_order_field = 'jackpot_amount_sol'
    
    def winner_link(self, obj):
        try:
            winner = obj.winner
            url = reverse('admin:yourapp_winner_change', args=[winner.id])
            return format_html('<a href="{}">🎉 Winner</a>', url)
        except Winner.DoesNotExist:
            return "No winner"
    winner_link.short_description = "Winner"

@admin.register(Winner)
class WinnerAdmin(admin.ModelAdmin):
    """Administration des gagnants"""
    list_display = ('wallet_short', 'lottery_link', 'winning_sol', 'payout_status_badge', 'created_at')
    list_filter = ('payout_status', 'created_at', 'lottery__lottery_type')
    search_fields = ('wallet_address', 'payout_transaction_signature')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
    
    fieldsets = (
        ('Winner Info', {
            'fields': ('lottery', 'wallet_address', 'tickets_held'),
            'classes': ('wide',)
        }),
        ('Winnings', {
            'fields': ('winning_amount_sol', 'winning_amount_usd'),
            'classes': ('wide',)
        }),
        ('Payout', {
            'fields': ('payout_status', 'payout_transaction_signature', 'payout_time'),
            'classes': ('wide',)
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def wallet_short(self, obj):
        return f"{obj.wallet_address[:8]}...{obj.wallet_address[-4:]}"
    wallet_short.short_description = "Winner Wallet"
    
    def lottery_link(self, obj):
        url = reverse('admin:yourapp_lottery_change', args=[obj.lottery.id])
        return format_html('<a href="{}">{}</a>', url, obj.lottery.get_lottery_type_display())
    lottery_link.short_description = "Lottery"
    
    def winning_sol(self, obj):
        return f"{obj.winning_amount_sol:.4f} SOL"
    winning_sol.short_description = "Winnings"
    winning_sol.admin_order_field = 'winning_amount_sol'
    
    def payout_status_badge(self, obj):
        colors = {
            'pending': '#ffc107',
            'processing': '#17a2b8',
            'completed': '#28a745',
            'failed': '#dc3545'
        }
        color = colors.get(obj.payout_status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color, obj.get_payout_status_display()
        )
    payout_status_badge.short_description = "Payout Status"

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Administration des transactions"""
    list_display = ('signature_short', 'transaction_type_badge', 'wallet_short', 'ball_amount_formatted', 'sol_amount_formatted', 'block_time')
    list_filter = ('transaction_type', 'block_time')
    search_fields = ('signature', 'wallet_address')
    ordering = ('-block_time',)
    readonly_fields = ('id', 'created_at', 'hourly_jackpot_contribution', 'daily_jackpot_contribution')
    
    fieldsets = (
        ('Transaction Info', {
            'fields': ('transaction_type', 'wallet_address', 'signature'),
            'classes': ('wide',)
        }),
        ('Amounts', {
            'fields': ('ball_amount', 'sol_amount', 'usd_amount'),
            'classes': ('wide',)
        }),
        ('Blockchain', {
            'fields': ('slot', 'block_time'),
        }),
        ('Jackpot Contributions', {
            'fields': ('hourly_jackpot_contribution', 'daily_jackpot_contribution'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    def signature_short(self, obj):
        return f"{obj.signature[:8]}...{obj.signature[-4:]}"
    signature_short.short_description = "Signature"
    
    def wallet_short(self, obj):
        return f"{obj.wallet_address[:8]}...{obj.wallet_address[-4:]}"
    wallet_short.short_description = "Wallet"
    
    def transaction_type_badge(self, obj):
        colors = {
            'buy': '#28a745',
            'sell': '#dc3545',
            'transfer': '#17a2b8',
            'payout': '#ffc107'
        }
        color = colors.get(obj.transaction_type, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color, obj.get_transaction_type_display()
        )
    transaction_type_badge.short_description = "Type"
    
    def ball_amount_formatted(self, obj):
        return f"{obj.ball_amount:,.2f} $BALL"
    ball_amount_formatted.short_description = "$BALL Amount"
    
    def sol_amount_formatted(self, obj):
        return f"{obj.sol_amount:.4f} SOL"
    sol_amount_formatted.short_description = "SOL Amount"

@admin.register(JackpotPool)
class JackpotPoolAdmin(admin.ModelAdmin):
    """Administration des pools de jackpot"""
    list_display = ('lottery_type', 'current_sol_formatted', 'current_usd_formatted', 'total_contributions_formatted', 'last_updated')
    list_filter = ('lottery_type', 'last_updated')
    readonly_fields = ('last_updated',)
    
    fieldsets = (
        ('Pool Info', {
            'fields': ('lottery_type',),
        }),
        ('Current Amounts', {
            'fields': ('current_amount_sol', 'current_amount_usd'),
            'classes': ('wide',)
        }),
        ('Statistics', {
            'fields': ('total_contributions', 'total_payouts'),
            'classes': ('wide',)
        }),
        ('Metadata', {
            'fields': ('last_updated',),
            'classes': ('collapse',)
        }),
    )
    
    def current_sol_formatted(self, obj):
        return f"{obj.current_amount_sol:.4f} SOL"
    current_sol_formatted.short_description = "Current SOL"
    
    def current_usd_formatted(self, obj):
        return f"${obj.current_amount_usd:,.2f}"
    current_usd_formatted.short_description = "Current USD"
    
    def total_contributions_formatted(self, obj):
        return f"{obj.total_contributions:.4f} SOL"
    total_contributions_formatted.short_description = "Total Contributions"

@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    """Administration de la configuration système"""
    list_display = ('key', 'value_short', 'is_active', 'updated_at')
    list_filter = ('is_active', 'updated_at')
    search_fields = ('key', 'value', 'description')
    ordering = ('key',)
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Configuration', {
            'fields': ('key', 'value', 'description', 'is_active'),
            'classes': ('wide',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def value_short(self, obj):
        return obj.value[:50] + "..." if len(obj.value) > 50 else obj.value
    value_short.short_description = "Value"

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Administration des logs d'audit"""
    list_display = ('action_type_badge', 'description_short', 'user', 'wallet_short', 'timestamp')
    list_filter = ('action_type', 'timestamp', 'user')
    search_fields = ('description', 'wallet_address', 'user__username')
    ordering = ('-timestamp',)
    readonly_fields = ('timestamp',)
    
    fieldsets = (
        ('Log Info', {
            'fields': ('action_type', 'description', 'user'),
            'classes': ('wide',)
        }),
        ('Related Objects', {
            'fields': ('lottery', 'wallet_address'),
        }),
        ('Metadata', {
            'fields': ('metadata', 'ip_address', 'timestamp'),
            'classes': ('collapse',)
        }),
    )
    
    def action_type_badge(self, obj):
        colors = {
            'lottery_created': '#17a2b8',
            'lottery_executed': '#28a745',
            'winner_selected': '#ffc107',
            'payout_sent': '#28a745',
            'jackpot_updated': '#6f42c1',
            'system_error': '#dc3545'
        }
        color = colors.get(obj.action_type, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color, obj.get_action_type_display()
        )
    action_type_badge.short_description = "Action"
    
    def description_short(self, obj):
        return obj.description[:80] + "..." if len(obj.description) > 80 else obj.description
    description_short.short_description = "Description"
    
    def wallet_short(self, obj):
        if obj.wallet_address:
            return f"{obj.wallet_address[:8]}...{obj.wallet_address[-4:]}"
        return "-"
    wallet_short.short_description = "Wallet"

# Configuration de l'admin site
admin.site.site_header = "PowerBall Lottery Administration"
admin.site.site_title = "PowerBall Admin"
admin.site.index_title = "Dashboard PowerBall"