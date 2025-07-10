from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from decimal import Decimal
import uuid
from django.utils import timezone

class User(AbstractUser):
    """Utilisateur personnalisé avec wallet Solana"""
    wallet_address = models.CharField(
        max_length=44, 
        unique=True, 
        null=True, 
        blank=True,
        help_text="Adresse du portefeuille Solana"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active_participant = models.BooleanField(default=True)
    
    # Résolution des conflits avec les relations ManyToMany
    groups = models.ManyToManyField(
        'auth.Group',
        verbose_name='groups',
        blank=True,
        help_text='The groups this user belongs to.',
        related_name='custom_user_set',  # Nom unique pour éviter les conflits
        related_query_name='custom_user',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name='user permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        related_name='custom_user_set',  # Nom unique pour éviter les conflits
        related_query_name='custom_user',
    )
    
    class Meta:
        # Suppression de db_table pour éviter le conflit avec auth.User
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    def __str__(self):
        return f"{self.username} ({self.wallet_address or 'No wallet'})"

class TokenHolding(models.Model):
    """Détention de tokens $BALL par wallet"""
    wallet_address = models.CharField(max_length=44, db_index=True)
    balance = models.DecimalField(
        max_digits=20, 
        decimal_places=8,
        validators=[MinValueValidator(Decimal('0'))]
    )
    tickets_count = models.IntegerField(
        default=0,
        help_text="Nombre de tickets (balance / 10000)"
    )
    last_updated = models.DateTimeField(auto_now=True)
    is_eligible = models.BooleanField(
        default=False,
        help_text="Éligible pour le prochain tirage"
    )
    
    class Meta:
        unique_together = ['wallet_address']
        indexes = [
            models.Index(fields=['wallet_address', 'is_eligible']),
            models.Index(fields=['tickets_count']),
        ]
        verbose_name = "Détention de Token"
        verbose_name_plural = "Détentions de Tokens"

    def save(self, *args, **kwargs):
        # Calcul automatique du nombre de tickets
        self.tickets_count = int(self.balance // 10000)
        self.is_eligible = self.tickets_count > 0
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.wallet_address[:8]}... - {self.tickets_count} tickets"

class LotteryType(models.TextChoices):
    """Types de loterie"""
    HOURLY = 'hourly', 'Hourly PowerBall'
    DAILY = 'daily', 'Mega Daily PowerBall'

class LotteryStatus(models.TextChoices):
    """Statuts des tirages"""
    PENDING = 'pending', 'En attente'
    PROCESSING = 'processing', 'En cours'
    COMPLETED = 'completed', 'Terminé'
    FAILED = 'failed', 'Échoué'

class Lottery(models.Model):
    """Tirage de loterie"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lottery_type = models.CharField(
        max_length=10,
        choices=LotteryType.choices,
        db_index=True
    )
    scheduled_time = models.DateTimeField(db_index=True)
    executed_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=15,
        choices=LotteryStatus.choices,
        default=LotteryStatus.PENDING,
        db_index=True
    )
    
    # Informations du jackpot
    jackpot_amount_sol = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        default=Decimal('0'),
        help_text="Montant du jackpot en SOL"
    )
    jackpot_amount_usd = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0'),
        help_text="Valeur estimée en USD"
    )
    
    # Participants
    total_participants = models.IntegerField(default=0)
    total_tickets = models.IntegerField(default=0)
    
    # Blockchain
    transaction_signature = models.CharField(
        max_length=88, 
        null=True, 
        blank=True,
        help_text="Signature de la transaction Solana"
    )
    slot_number = models.BigIntegerField(
        null=True, 
        blank=True,
        help_text="Numéro de slot Solana"
    )
    
    # VRF
    vrf_request_id = models.CharField(max_length=100, null=True, blank=True)
    random_seed = models.CharField(max_length=64, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-scheduled_time']
        indexes = [
            models.Index(fields=['lottery_type', 'status']),
            models.Index(fields=['scheduled_time', 'status']),
        ]
        verbose_name = "Tirage"
        verbose_name_plural = "Tirages"

    def __str__(self):
        return f"{self.get_lottery_type_display()} - {self.scheduled_time}"

    @property
    def is_active(self):
        return self.status in [LotteryStatus.PENDING, LotteryStatus.PROCESSING]

class Winner(models.Model):
    """Gagnant d'un tirage"""
    lottery = models.OneToOneField(
        Lottery, 
        on_delete=models.CASCADE,
        related_name='winner'
    )
    wallet_address = models.CharField(max_length=44, db_index=True)
    winning_amount_sol = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        validators=[MinValueValidator(Decimal('0'))]
    )
    winning_amount_usd = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0')
    )
    tickets_held = models.IntegerField(
        validators=[MinValueValidator(1)],
        help_text="Nombre de tickets détenus au moment du tirage"
    )
    
    # Transaction de paiement
    payout_transaction_signature = models.CharField(
        max_length=88, 
        null=True, 
        blank=True
    )
    payout_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'En attente'),
            ('processing', 'En cours'),
            ('completed', 'Payé'),
            ('failed', 'Échec'),
        ],
        default='pending'
    )
    payout_time = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['wallet_address']),
            models.Index(fields=['payout_status']),
        ]
        verbose_name = "Gagnant"
        verbose_name_plural = "Gagnants"

    def __str__(self):
        return f"{self.wallet_address[:8]}... - {self.winning_amount_sol} SOL"

class Transaction(models.Model):
    """Transactions liées aux achats de tokens $BALL"""
    TRANSACTION_TYPES = [
        ('buy', 'Achat $BALL'),
        ('sell', 'Vente $BALL'),
        ('transfer', 'Transfert'),
        ('payout', 'Paiement de gain'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    wallet_address = models.CharField(max_length=44, db_index=True)
    
    # Montants
    ball_amount = models.DecimalField(
        max_digits=20, 
        decimal_places=8,
        default=Decimal('0')
    )
    sol_amount = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        default=Decimal('0')
    )
    usd_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0')
    )
    
    # Blockchain
    signature = models.CharField(max_length=88, unique=True)
    slot = models.BigIntegerField()
    block_time = models.DateTimeField()
    
    # Contribution aux jackpots (pour les achats)
    hourly_jackpot_contribution = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        default=Decimal('0'),
        help_text="10% pour le jackpot horaire"
    )
    daily_jackpot_contribution = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        default=Decimal('0'),
        help_text="5% pour le jackpot journalier"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-block_time']
        indexes = [
            models.Index(fields=['wallet_address', 'transaction_type']),
            models.Index(fields=['signature']),
            models.Index(fields=['block_time']),
        ]
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"

    def save(self, *args, **kwargs):
        if self.transaction_type == 'buy' and self.sol_amount > 0:
            # Calcul automatique des contributions
            self.hourly_jackpot_contribution = self.sol_amount * Decimal('0.10')
            self.daily_jackpot_contribution = self.sol_amount * Decimal('0.05')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.signature[:8]}... - {self.get_transaction_type_display()}"

class JackpotPool(models.Model):
    """Pool de jackpot en temps réel"""
    lottery_type = models.CharField(
        max_length=10,
        choices=LotteryType.choices,
        unique=True
    )
    current_amount_sol = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        default=Decimal('0')
    )
    current_amount_usd = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0')
    )
    total_contributions = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        default=Decimal('0'),
        help_text="Total des contributions historiques"
    )
    total_payouts = models.DecimalField(
        max_digits=15, 
        decimal_places=9,
        default=Decimal('0'),
        help_text="Total des gains distribués"
    )
    last_updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Pool de Jackpot"
        verbose_name_plural = "Pools de Jackpot"

    def __str__(self):
        return f"{self.get_lottery_type_display()} - {self.current_amount_sol} SOL"

class SystemConfig(models.Model):
    """Configuration système"""
    key = models.CharField(max_length=50, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Configuration Système"
        verbose_name_plural = "Configurations Système"

    def __str__(self):
        return f"{self.key}: {self.value[:50]}"

class AuditLog(models.Model):
    """Log d'audit pour traçabilité"""
    ACTION_TYPES = [
        ('lottery_created', 'Tirage créé'),
        ('lottery_executed', 'Tirage exécuté'),
        ('winner_selected', 'Gagnant sélectionné'),
        ('payout_sent', 'Gain envoyé'),
        ('jackpot_updated', 'Jackpot mis à jour'),
        ('system_error', 'Erreur système'),
    ]
    
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    description = models.TextField()
    user = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    lottery = models.ForeignKey(
        Lottery, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    wallet_address = models.CharField(max_length=44, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['action_type', 'timestamp']),
            models.Index(fields=['wallet_address']),
        ]
        verbose_name = "Log d'Audit"
        verbose_name_plural = "Logs d'Audit"

    def __str__(self):
        return f"{self.get_action_type_display()} - {self.timestamp}"