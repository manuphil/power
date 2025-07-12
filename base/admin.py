from django.contrib import admin
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.urls import reverse
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    User, TokenHolding, Lottery, Winner, Transaction,
    JackpotPool, SystemConfig, AuditLog, LotteryType, LotteryStatus
)

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin simplifié pour les utilisateurs"""
    list_display = ['username', 'email', 'wallet_address_short', 'is_active_participant', 'created_at']
    list_filter = ['is_active_participant', 'is_active', 'created_at']
    search_fields = ['username', 'email', 'wallet_address']
    readonly_fields = ['created_at', 'updated_at']
    
    # Fieldsets simplifiés
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Wallet Info', {'fields': ('wallet_address', 'is_active_participant')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    
    def wallet_address_short(self, obj):
        if obj.wallet_address:
            return f"{obj.wallet_address[:8]}...{obj.wallet_address[-6:]}"
        return "No wallet"
    wallet_address_short.short_description = 'Wallet'

@admin.register(TokenHolding)
class TokenHoldingAdmin(admin.ModelAdmin):
    """Admin simplifié pour les détentions de tokens"""
    list_display = ['wallet_address_short', 'balance', 'tickets_count', 'is_eligible', 'last_updated']
    list_filter = ['is_eligible']
    search_fields = ['wallet_address']
    readonly_fields = ['tickets_count', 'is_eligible', 'last_updated']
    
    def wallet_address_short(self, obj):
        return f"{obj.wallet_address[:8]}...{obj.wallet_address[-6:]}"
    wallet_address_short.short_description = 'Wallet'

@admin.register(Lottery)
class LotteryAdmin(admin.ModelAdmin):
    """Admin SIMPLIFIÉ pour les tirages - Version Debug"""
    list_display = ['id_short', 'lottery_type', 'status', 'scheduled_time', 'jackpot_amount_sol']
    list_filter = ['lottery_type', 'status']
    search_fields = ['id']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    # CHAMPS MINIMAUX pour éviter les erreurs
    fields = [
        'lottery_type',
        'scheduled_time', 
        'status',
        'jackpot_amount_sol',
        'jackpot_amount_usd',
        'total_participants',
        'total_tickets',
        'id',
        'created_at',
        'updated_at'
    ]
    
    def id_short(self, obj):
        return str(obj.id)[:8] if obj.id else "New"
    id_short.short_description = 'ID'
    
    def get_form(self, request, obj=None, **kwargs):
        """Formulaire avec valeurs par défaut sécurisées"""
        form = super().get_form(request, obj, **kwargs)
        
        if not obj:  # Nouveau tirage uniquement
            # Valeurs par défaut sécurisées
            form.base_fields['status'].initial = LotteryStatus.PENDING
            form.base_fields['scheduled_time'].initial = timezone.now() + timezone.timedelta(hours=1)
            form.base_fields['jackpot_amount_sol'].initial = '0.001'
            form.base_fields['jackpot_amount_usd'].initial = '0.10'
            form.base_fields['total_participants'].initial = 0
            form.base_fields['total_tickets'].initial = 0
            
        return form
    
    def save_model(self, request, obj, form, change):
        """Sauvegarde avec debug détaillé"""
        try:
            print(f"\n🔍 DEBUG ADMIN SAVE:")
            print(f"  - Object: {obj}")
            print(f"  - Change: {change}")
            print(f"  - Status: {obj.status}")
            print(f"  - Type: {obj.lottery_type}")
            print(f"  - Scheduled: {obj.scheduled_time}")
            
            # Validation automatique pour les tirages "completed"
            if obj.status == LotteryStatus.COMPLETED and not obj.executed_time:
                obj.executed_time = timezone.now()
                print(f"  - Auto-set executed_time: {obj.executed_time}")
            
            # Sauvegarder avec transaction
            from django.db import transaction
            with transaction.atomic():
                super().save_model(request, obj, form, change)
                print(f"✅ Lottery sauvegardée avec succès: {obj.id}")
            
        except Exception as e:
            print(f"❌ ERREUR dans save_model: {e}")
            print(f"❌ Type d'erreur: {type(e)}")
            import traceback
            traceback.print_exc()
            raise ValidationError(f"Erreur de sauvegarde: {str(e)}")

@admin.register(Winner)
class WinnerAdmin(admin.ModelAdmin):
    """Admin simplifié pour les gagnants"""
    list_display = ['wallet_address_short', 'winning_amount_sol', 'payout_status', 'created_at']
    list_filter = ['payout_status', 'created_at']
    search_fields = ['wallet_address']
    readonly_fields = ['created_at']
    
    def wallet_address_short(self, obj):
        return f"{obj.wallet_address[:8]}...{obj.wallet_address[-6:]}"
    wallet_address_short.short_description = 'Wallet'

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Admin simplifié pour les transactions"""
    list_display = ['signature_short', 'transaction_type', 'wallet_address_short', 'sol_amount', 'block_time']
    list_filter = ['transaction_type']
    search_fields = ['wallet_address', 'signature']
    readonly_fields = ['id', 'created_at', 'hourly_jackpot_contribution', 'daily_jackpot_contribution']
    
    def signature_short(self, obj):
        return f"{obj.signature[:8]}...{obj.signature[-6:]}"
    signature_short.short_description = 'Signature'
    
    def wallet_address_short(self, obj):
        return f"{obj.wallet_address[:8]}...{obj.wallet_address[-6:]}"
    wallet_address_short.short_description = 'Wallet'

@admin.register(JackpotPool)
class JackpotPoolAdmin(admin.ModelAdmin):
    """Admin simplifié pour les pools"""
    list_display = ['lottery_type', 'current_amount_sol', 'last_updated']
    readonly_fields = ['last_updated']

@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    """Admin simplifié pour la config"""
    list_display = ['key', 'value_short', 'is_active']
    search_fields = ['key']
    readonly_fields = ['created_at', 'updated_at']
    
    def value_short(self, obj):
        return obj.value[:50] + '...' if len(obj.value) > 50 else obj.value
    value_short.short_description = 'Value'

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Admin en lecture seule pour les logs"""
    list_display = ['timestamp', 'action_type', 'description_short']
    list_filter = ['action_type']
    readonly_fields = ['timestamp']
    ordering = ['-timestamp']
    
    def description_short(self, obj):
        return obj.description[:50] + '...' if len(obj.description) > 50 else obj.description
    description_short.short_description = 'Description'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False

# Configuration du site admin
admin.site.site_header = "Lottery Solana Administration"
admin.site.site_title = "Lottery Admin"
admin.site.index_title = "Administration de la Loterie"
