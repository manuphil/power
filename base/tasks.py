from celery import shared_task
from django.utils import timezone
from django.db.models import Sum, Avg
from datetime import timedelta, date
import asyncio
import logging
import secrets
from decimal import Decimal

from .models import (
    Lottery, Winner, Transaction, TokenHolding,
    JackpotPool, LotteryType, AuditLog
)
from .solana_service import solana_service

logger = logging.getLogger(__name__)

# 🔹 PRODUCTION: Helper pour gérer les boucles asyncio de manière sécurisée
def run_async_task(coro):
    """Exécute une coroutine de manière sécurisée dans Celery"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        return result
    except Exception as e:
        logger.error(f"Async task error: {e}")
        raise
    finally:
        loop.close()

@shared_task
def sync_lottery_state():
    """Synchronise l'état de la loterie avec Solana"""
    try:
        result = run_async_task(solana_service.sync_lottery_state())
        
        if result:
            logger.info("PRODUCTION: Lottery state synchronized successfully")
            return result
        else:
            logger.error("PRODUCTION: Failed to sync lottery state")
            return None
    except Exception as e:
        logger.error(f"PRODUCTION ERROR syncing lottery state: {e}")
        raise  # 🔹 Lever l'erreur pour alerter les admins

@shared_task
def sync_participant_holdings():
    """Synchronise les détentions de tokens avec Solana - PRODUCTION SAFE"""
    try:
        # 🔹 PRODUCTION: Limiter le nombre de participants traités par batch
        participants = TokenHolding.objects.filter(is_eligible=True)[:100]  # Batch de 100
        synced_count = 0
        failed_count = 0

        for participant in participants:
            try:
                result = run_async_task(
                    solana_service.sync_participant(participant.wallet_address)
                )
                if result:
                    synced_count += 1
                else:
                    failed_count += 1
                    logger.warning(f"Failed to sync participant: {participant.wallet_address}")
                    
            except Exception as e:
                failed_count += 1
                logger.error(f"Error syncing participant {participant.wallet_address}: {e}")
                continue

        logger.info(f"PRODUCTION: Synchronized {synced_count}/{participants.count()} participants, {failed_count} failed")
        return {'synced': synced_count, 'failed': failed_count}
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR syncing participants: {e}")
        raise

@shared_task
def sync_single_participant(wallet_address):
    """Synchronise un participant spécifique avec validation"""
    try:
        result = run_async_task(solana_service.sync_participant(wallet_address))
        
        if result:
            logger.info(f"PRODUCTION: Participant {wallet_address} synchronized successfully")
            return True
        else:
            logger.error(f"PRODUCTION: Failed to sync participant {wallet_address}")
            return False
            
    except Exception as e:
        logger.error(f"PRODUCTION ERROR syncing participant {wallet_address}: {e}")
        raise

@shared_task
def create_scheduled_lotteries():
    """Crée les tirages programmés avec validation blockchain"""
    try:
        now = timezone.now()
        created_count = 0

        # 🔹 PRODUCTION: Vérifier d'abord l'état de la blockchain
        lottery_state = run_async_task(solana_service.get_lottery_state())
        if not lottery_state:
            logger.error("PRODUCTION: Cannot create lotteries - blockchain state unavailable")
            return 0

        # Créer les tirages horaires
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        
        if not Lottery.objects.filter(
            lottery_type=LotteryType.HOURLY,
            scheduled_time=next_hour,
            status='pending'
        ).exists():
            
            # 🔹 PRODUCTION: Utiliser les données blockchain réelles
            jackpot_amount = Decimal(str(lottery_state['hourly_jackpot'])) / Decimal('1000000000')
            
            if jackpot_amount >= Decimal('0.001'):  # Minimum 0.001 SOL
                hourly_lottery = Lottery.objects.create(
                    lottery_type=LotteryType.HOURLY,
                    scheduled_time=next_hour,
                    jackpot_amount_sol=jackpot_amount,
                    total_participants=lottery_state['total_participants'],
                    status='pending'
                )
                created_count += 1
                logger.info(f"PRODUCTION: Created hourly lottery for {next_hour} with {jackpot_amount} SOL")

        # Créer les tirages journaliers
        if now.hour == 11 and now.minute >= 50:
            next_day_noon = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
            
            if not Lottery.objects.filter(
                lottery_type=LotteryType.DAILY,
                scheduled_time=next_day_noon,
                status='pending'
            ).exists():
                
                jackpot_amount = Decimal(str(lottery_state['daily_jackpot'])) / Decimal('1000000000')
                
                if jackpot_amount >= Decimal('0.001'):
                    daily_lottery = Lottery.objects.create(
                        lottery_type=LotteryType.DAILY,
                        scheduled_time=next_day_noon,
                        jackpot_amount_sol=jackpot_amount,
                        total_participants=lottery_state['total_participants'],
                        status='pending'
                    )
                    created_count += 1
                    logger.info(f"PRODUCTION: Created daily lottery for {next_day_noon} with {jackpot_amount} SOL")

        return created_count
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR creating scheduled lotteries: {e}")
        raise

@shared_task
def execute_pending_lotteries():
    """Exécute les tirages en attente avec sélection sécurisée du gagnant"""
    try:
        now = timezone.now()
        pending_lotteries = Lottery.objects.filter(
            status='pending',
            scheduled_time__lte=now
        )
        
        executed_count = 0
        
        for lottery in pending_lotteries:
            try:
                # 🔹 PRODUCTION: Vérifier les participants éligibles sur la blockchain
                eligible_participants = TokenHolding.objects.filter(
                    is_eligible=True,
                    tickets_count__gt=0
                )
                
                if not eligible_participants.exists():
                    logger.warning(f"PRODUCTION: No eligible participants for lottery {lottery.id}")
                    continue

                # 🔹 PRODUCTION: Sélection sécurisée du gagnant
                winner = select_lottery_winner_secure(eligible_participants)
                if not winner:
                    logger.error(f"PRODUCTION: Failed to select winner for lottery {lottery.id}")
                    continue

                # 🔹 PRODUCTION: Créer la loterie sur la blockchain d'abord
                success = run_async_task(
                    solana_service.execute_lottery_on_chain(lottery, winner.wallet_address)
                )
                
                if success:
                    executed_count += 1
                    logger.info(f"PRODUCTION: Lottery {lottery.id} executed successfully, winner: {winner.wallet_address}")
                    
                    # Audit log
                    AuditLog.objects.create(
                        action_type='lottery_executed',
                        description=f'PRODUCTION: Tirage {lottery.id} exécuté automatiquement',
                        lottery=lottery,
                        wallet_address=winner.wallet_address,
                        metadata={
                            'lottery_type': lottery.lottery_type,
                            'jackpot_amount': str(lottery.jackpot_amount_sol),
                            'winner_tickets': winner.tickets_count,
                            'total_participants': lottery.total_participants
                        }
                    )
                else:
                    lottery.status = 'failed'
                    lottery.save()
                    logger.error(f"PRODUCTION: Failed to execute lottery {lottery.id}")
                    
            except Exception as e:
                logger.error(f"PRODUCTION ERROR executing lottery {lottery.id}: {e}")
                lottery.status = 'failed'
                lottery.save()
                continue

        return executed_count
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR executing pending lotteries: {e}")
        raise

@shared_task
def process_pending_payouts():
    """Traite les paiements en attente avec validation blockchain"""
    try:
        pending_winners = Winner.objects.filter(payout_status='pending')
        processed = 0
        failed = 0

        for winner in pending_winners:
            try:
                # 🔹 PRODUCTION: Valider avant paiement
                success = run_async_task(solana_service.pay_winner_on_chain(winner))
                
                if success:
                    processed += 1
                    logger.info(f"PRODUCTION: Winner {winner.wallet_address} paid successfully")
                    
                    # Audit log
                    AuditLog.objects.create(
                        action_type='payout_sent',
                        description=f'PRODUCTION: Gagnant {winner.wallet_address} payé automatiquement',
                        lottery=winner.lottery,
                        wallet_address=winner.wallet_address,
                        metadata={
                            'amount': str(winner.winning_amount_sol),
                            'lottery_type': winner.lottery.lottery_type,
                            'transaction_signature': winner.payout_transaction_signature
                        }
                    )
                else:
                    failed += 1
                    logger.error(f"PRODUCTION: Failed to pay winner {winner.wallet_address}")
                    
            except Exception as e:
                failed += 1
                logger.error(f"PRODUCTION ERROR paying winner {winner.wallet_address}: {e}")
                continue

        logger.info(f"PRODUCTION: Processed {processed} payouts, {failed} failed")
        return {'processed': processed, 'failed': failed}
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR processing payouts: {e}")
        raise

@shared_task
def monitor_blockchain_events():
    """Surveille les événements de la blockchain en production"""
    try:
        new_transactions = run_async_task(solana_service.get_recent_transactions())
        processed = 0

        for tx_data in new_transactions:
            try:
                # 🔹 PRODUCTION: Validation stricte des transactions
                if not tx_data.get('signature') or not tx_data.get('wallet'):
                    continue

                transaction, created = Transaction.objects.get_or_create(
                    signature=tx_data['signature'],
                    defaults={
                        'transaction_type': tx_data['type'],
                        'wallet_address': tx_data['wallet'],
                        'ball_amount': tx_data.get('ball_amount', 0),
                        'sol_amount': tx_data.get('sol_amount', 0),
                        'slot': tx_data['slot'],
                        'block_time': tx_data['block_time']
                    }
                )
                
                if created:
                    processed += 1
                    
                    # 🔹 PRODUCTION: Synchroniser seulement les achats validés
                    if tx_data['type'] == 'buy' and tx_data.get('ball_amount', 0) > 0:
                        sync_single_participant.delay(tx_data['wallet'])
                        
            except Exception as e:
                logger.error(f"PRODUCTION ERROR processing transaction {tx_data.get('signature', 'unknown')}: {e}")
                continue

        logger.info(f"PRODUCTION: Processed {processed} new blockchain transactions")
        return processed
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR monitoring blockchain events: {e}")
        raise

# 🔹 PRODUCTION: Sélection sécurisée du gagnant
def select_lottery_winner_secure(eligible_participants):
    """Sélectionne un gagnant avec cryptographie sécurisée"""
    try:
        # Créer une liste pondérée
        weighted_participants = []
        total_tickets = 0
        
        for participant in eligible_participants:
            tickets = max(1, participant.tickets_count)  # Minimum 1 ticket
            for _ in range(tickets):
                weighted_participants.append(participant)
            total_tickets += tickets

        if not weighted_participants:
            return None

        # 🔹 PRODUCTION: Utiliser secrets au lieu de random
        secure_index = secrets.randbelow(len(weighted_participants))
        winner = weighted_participants[secure_index]
        
        logger.info(f"PRODUCTION: Selected winner {winner.wallet_address} from {total_tickets} total tickets")
        return winner
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR selecting winner: {e}")
        return None

@shared_task
def health_check():
    """Vérification de santé du système en production"""
    try:
        health_status = {
            'timestamp': timezone.now().isoformat(),
            'database': True,
            'solana': False,
            'celery': True,
            'issues': []
        }

        # Vérifier la base de données
        try:
            Lottery.objects.count()
        except Exception as e:
            health_status['database'] = False
            health_status['issues'].append(f"Database error: {e}")

        # 🔹 PRODUCTION: Vérification Solana stricte
        try:
            connection_ok = run_async_task(solana_service.check_connection())
            health_status['solana'] = connection_ok
            
            if not connection_ok:
                health_status['issues'].append("PRODUCTION: Solana connection failed")
                
        except Exception as e:
            health_status['solana'] = False
            health_status['issues'].append(f"PRODUCTION: Solana error: {e}")

        # Vérifier les tirages en retard
        overdue_lotteries = Lottery.objects.filter(
            status='pending',
            scheduled_time__lt=timezone.now() - timedelta(minutes=5)
        ).count()
        
        if overdue_lotteries > 0:
            health_status['issues'].append(f"PRODUCTION: {overdue_lotteries} overdue lotteries")

        # Vérifier les paiements en attente
        old_pending_payouts = Winner.objects.filter(
            payout_status='pending',
            created_at__lt=timezone.now() - timedelta(hours=1)
        ).count()
        if old_pending_payouts > 0:
            health_status['issues'].append(f"PRODUCTION: {old_pending_payouts} old pending payouts")

        # 🔹 PRODUCTION: Vérifier l'état des jackpots
        try:
            lottery_state = run_async_task(solana_service.get_lottery_state())
            if lottery_state:
                hourly_jackpot = Decimal(str(lottery_state['hourly_jackpot'])) / Decimal('1000000000')
                daily_jackpot = Decimal(str(lottery_state['daily_jackpot'])) / Decimal('1000000000')
                
                if hourly_jackpot < Decimal('0.001'):
                    health_status['issues'].append("PRODUCTION: Hourly jackpot too low")
                if daily_jackpot < Decimal('0.001'):
                    health_status['issues'].append("PRODUCTION: Daily jackpot too low")
            else:
                health_status['issues'].append("PRODUCTION: Cannot fetch lottery state")
        except Exception as e:
            health_status['issues'].append(f"PRODUCTION: Jackpot check error: {e}")

        # Log des problèmes critiques
        if health_status['issues']:
            logger.error(f"PRODUCTION HEALTH CHECK ISSUES: {health_status['issues']}")
        else:
            logger.info("PRODUCTION: Health check passed")

        return health_status
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR in health check: {e}")
        return {
            'timestamp': timezone.now().isoformat(),
            'database': False,
            'solana': False,
            'celery': False,
            'issues': [f"PRODUCTION: Health check failed: {e}"]
        }

@shared_task
def update_jackpot_pools():
    """Met à jour les pools de jackpot avec données blockchain réelles"""
    try:
        # 🔹 PRODUCTION: Synchroniser avec les données on-chain
        lottery_state = run_async_task(solana_service.get_lottery_state())
        
        if not lottery_state:
            logger.error("PRODUCTION: Cannot update jackpot pools - blockchain unavailable")
            return None

        # Convertir de lamports en SOL
        hourly_sol = Decimal(str(lottery_state['hourly_jackpot'])) / Decimal('1000000000')
        daily_sol = Decimal(str(lottery_state['daily_jackpot'])) / Decimal('1000000000')

        # Mettre à jour le pool horaire
        hourly_pool, _ = JackpotPool.objects.get_or_create(
            lottery_type=LotteryType.HOURLY,
            defaults={'current_amount_sol': Decimal('0')}
        )
        hourly_pool.current_amount_sol = hourly_sol
        hourly_pool.last_updated = timezone.now()
        hourly_pool.save()

        # Mettre à jour le pool journalier
        daily_pool, _ = JackpotPool.objects.get_or_create(
            lottery_type=LotteryType.DAILY,
            defaults={'current_amount_sol': Decimal('0')}
        )
        daily_pool.current_amount_sol = daily_sol
        daily_pool.last_updated = timezone.now()
        daily_pool.save()

        logger.info(f"PRODUCTION: Jackpot pools updated - Hourly: {hourly_sol} SOL, Daily: {daily_sol} SOL")
        
        return {
            'hourly': str(hourly_sol),
            'daily': str(daily_sol),
            'total_participants': lottery_state['total_participants'],
            'total_tickets': lottery_state['total_tickets']
        }
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR updating jackpot pools: {e}")
        raise

@shared_task
def cleanup_old_data():
    """Nettoie les anciennes données avec sécurité production"""
    try:
        cutoff_date = timezone.now() - timedelta(days=90)
        results = {
            'logs_deleted': 0,
            'transactions_deleted': 0,
            'participants_cleaned': 0,
            'errors': []
        }

        # 🔹 PRODUCTION: Supprimer les anciens logs d'audit par batch
        try:
            old_logs = AuditLog.objects.filter(timestamp__lt=cutoff_date)
            logs_count = old_logs.count()
            
            # Supprimer par batch de 1000
            batch_size = 1000
            deleted_total = 0
            while old_logs.exists():
                batch_ids = list(old_logs.values_list('id', flat=True)[:batch_size])
                deleted_count = AuditLog.objects.filter(id__in=batch_ids).delete()[0]
                deleted_total += deleted_count
                if deleted_count == 0:
                    break
                    
            results['logs_deleted'] = deleted_total
            logger.info(f"PRODUCTION: Deleted {deleted_total} old audit logs")
            
        except Exception as e:
            results['errors'].append(f"Audit logs cleanup error: {e}")

        # 🔹 PRODUCTION: Nettoyer les anciennes transactions
        try:
            transaction_cutoff = timezone.now() - timedelta(days=30)
            old_transactions = Transaction.objects.filter(block_time__lt=transaction_cutoff)
            transactions_count = old_transactions.delete()[0]
            results['transactions_deleted'] = transactions_count
            logger.info(f"PRODUCTION: Deleted {transactions_count} old transactions")
            
        except Exception as e:
            results['errors'].append(f"Transactions cleanup error: {e}")

        # 🔹 PRODUCTION: Nettoyer les participants inactifs (avec prudence)
        try:
            inactive_cutoff = timezone.now() - timedelta(days=60)  # Plus conservateur
            inactive_participants = TokenHolding.objects.filter(
                balance=0,
                tickets_count=0,
                is_eligible=False,
                last_updated__lt=inactive_cutoff
            )
            participants_count = inactive_participants.delete()[0]
            results['participants_cleaned'] = participants_count
            logger.info(f"PRODUCTION: Cleaned {participants_count} inactive participants")
            
        except Exception as e:
            results['errors'].append(f"Participants cleanup error: {e}")

        if results['errors']:
            logger.warning(f"PRODUCTION: Cleanup completed with errors: {results['errors']}")
        else:
            logger.info(f"PRODUCTION: Cleanup completed successfully: {results}")

        return results
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR cleaning up old data: {e}")
        raise

@shared_task
def send_lottery_notifications():
    """Envoie des notifications pour les tirages en production"""
    try:
        from django.core.mail import send_mail
        from django.conf import settings

        # 🔹 PRODUCTION: Notifications pour les tirages dans 10 minutes
        upcoming_lotteries = Lottery.objects.filter(
            status='pending',
            scheduled_time__gte=timezone.now() + timedelta(minutes=9),
            scheduled_time__lte=timezone.now() + timedelta(minutes=11)
        )

        notifications_sent = 0
        
        for lottery in upcoming_lotteries:
            try:
                # 🔹 PRODUCTION: Vérifier l'état du jackpot avant notification
                if lottery.jackpot_amount_sol < Decimal('0.001'):
                    logger.warning(f"PRODUCTION: Skipping notification for low jackpot lottery {lottery.id}")
                    continue

                # Notification aux admins
                if hasattr(settings, 'ADMIN_EMAIL') and settings.ADMIN_EMAIL:
                    subject = f'PRODUCTION: Tirage {lottery.get_lottery_type_display()} dans 10 minutes'
                    message = f"""
PRODUCTION LOTTERY NOTIFICATION

Tirage ID: {lottery.id}
Type: {lottery.get_lottery_type_display()}
Heure prévue: {lottery.scheduled_time}
Jackpot: {lottery.jackpot_amount_sol} SOL
Participants: {lottery.total_participants}
Tickets total: {TokenHolding.objects.filter(is_eligible=True).aggregate(Sum('tickets_count'))['tickets_count__sum'] or 0}

Système: Production
                    """
                    
                    send_mail(
                        subject=subject,
                        message=message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[settings.ADMIN_EMAIL],
                        fail_silently=False  # 🔹 PRODUCTION: Ne pas ignorer les erreurs
                    )
                    notifications_sent += 1
                    logger.info(f"PRODUCTION: Notification sent for lottery {lottery.id}")
                    
            except Exception as e:
                logger.error(f"PRODUCTION ERROR sending notification for lottery {lottery.id}: {e}")
                continue

        return notifications_sent
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR sending notifications: {e}")
        raise

@shared_task
def generate_lottery_reports():
    """Génère des rapports de loterie pour la production"""
    try:
        today = date.today()
        yesterday = today - timedelta(days=1)

        # 🔹 PRODUCTION: Rapport quotidien détaillé
        daily_report = {
            'date': yesterday.isoformat(),
            'environment': 'PRODUCTION',
            'lotteries_completed': Lottery.objects.filter(
                executed_time__date=yesterday,
                status='completed'
            ).count(),
            'lotteries_failed': Lottery.objects.filter(
                executed_time__date=yesterday,
                status='failed'
            ).count(),
            'total_winnings_sol': Winner.objects.filter(
                created_at__date=yesterday,
                payout_status='completed'
            ).aggregate(total=Sum('winning_amount_sol'))['total'] or Decimal('0'),
            'pending_payouts': Winner.objects.filter(
                created_at__date=yesterday,
                payout_status='pending'
            ).count(),
            'new_participants': TokenHolding.objects.filter(
                last_updated__date=yesterday,
                is_eligible=True
            ).count(),
            'total_transactions': Transaction.objects.filter(
                block_time__date=yesterday
            ).count(),
            'active_participants': TokenHolding.objects.filter(
                is_eligible=True
            ).count(),
            'total_tickets': TokenHolding.objects.filter(
                is_eligible=True
            ).aggregate(total=Sum('tickets_count'))['total'] or 0
        }

        # 🔹 PRODUCTION: Ajouter l'état actuel des jackpots
        try:
            lottery_state = run_async_task(solana_service.get_lottery_state())
            if lottery_state:
                daily_report['current_hourly_jackpot'] = str(
                    Decimal(str(lottery_state['hourly_jackpot'])) / Decimal('1000000000')
                )
                daily_report['current_daily_jackpot'] = str(
                    Decimal(str(lottery_state['daily_jackpot'])) / Decimal('1000000000')
                )
        except Exception as e:
            daily_report['jackpot_error'] = str(e)

        logger.info(f"PRODUCTION: Daily report generated: {daily_report}")

        # 🔹 PRODUCTION: Rapport hebdomadaire (le dimanche)
        if today.weekday() == 6:  # Dimanche
            week_start = today - timedelta(days=7)
            
            weekly_report = {
                'week_start': week_start.isoformat(),
                'week_end': yesterday.isoformat(),
                'environment': 'PRODUCTION',
                'total_lotteries': Lottery.objects.filter(
                    executed_time__date__range=[week_start, yesterday],
                    status='completed'
                ).count(),
                'total_winnings_sol': Winner.objects.filter(
                    created_at__date__range=[week_start, yesterday],
                    payout_status='completed'
                ).aggregate(total=Sum('winning_amount_sol'))['total'] or Decimal('0'),
                'active_participants': TokenHolding.objects.filter(
                    is_eligible=True
                ).count(),
                'avg_jackpot_sol': Lottery.objects.filter(
                    executed_time__date__range=[week_start, yesterday],
                    status='completed'
                ).aggregate(avg=Avg('jackpot_amount_sol'))['avg'] or Decimal('0'),
                'success_rate': 0
            }
            
            # Calculer le taux de succès
            total_lotteries = Lottery.objects.filter(
                executed_time__date__range=[week_start, yesterday]
            ).count()
            if total_lotteries > 0:
                weekly_report['success_rate'] = (
                    weekly_report['total_lotteries'] / total_lotteries * 100
                )

            logger.info(f"PRODUCTION: Weekly report generated: {weekly_report}")

        return daily_report
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR generating reports: {e}")
        raise

@shared_task
def validate_blockchain_consistency():
    """Valide la cohérence entre la base de données et la blockchain"""
    try:
        inconsistencies = []
        
        # 🔹 PRODUCTION: Vérifier l'état des jackpots
        try:
            lottery_state = run_async_task(solana_service.get_lottery_state())
            if lottery_state:
                # Vérifier les pools horaires
                hourly_pool = JackpotPool.objects.filter(lottery_type=LotteryType.HOURLY).first()
                if hourly_pool:
                    blockchain_hourly = Decimal(str(lottery_state['hourly_jackpot'])) / Decimal('1000000000')
                    if abs(hourly_pool.current_amount_sol - blockchain_hourly) > Decimal('0.001'):
                        inconsistencies.append(f"Hourly jackpot mismatch: DB={hourly_pool.current_amount_sol}, Blockchain={blockchain_hourly}")

                # Vérifier les pools journaliers
                daily_pool = JackpotPool.objects.filter(lottery_type=LotteryType.DAILY).first()
                if daily_pool:
                    blockchain_daily = Decimal(str(lottery_state['daily_jackpot'])) / Decimal('1000000000')
                    if abs(daily_pool.current_amount_sol - blockchain_daily) > Decimal('0.001'):
                        inconsistencies.append(f"Daily jackpot mismatch: DB={daily_pool.current_amount_sol}, Blockchain={blockchain_daily}")
                        
        except Exception as e:
            inconsistencies.append(f"Jackpot validation error: {e}")

        # 🔹 PRODUCTION: Vérifier les participants actifs
        try:
            active_participants = TokenHolding.objects.filter(is_eligible=True)[:10]  # Sample
            for participant in active_participants:
                try:
                    blockchain_info = run_async_task(
                        solana_service.get_participant_info(participant.wallet_address)
                    )
                    
                    if blockchain_info:
                        
                        
                        blockchain_balance = Decimal(str(blockchain_info['ball_balance'])) / Decimal('100000000')
                        blockchain_tickets = blockchain_info['tickets_count']
                        
                        # Vérifier les différences significatives
                        if abs(participant.balance - blockchain_balance) > Decimal('0.01'):
                            inconsistencies.append(
                                f"Participant {participant.wallet_address} balance mismatch: "
                                f"DB={participant.balance}, Blockchain={blockchain_balance}"
                            )
                            
                        if participant.tickets_count != blockchain_tickets:
                            inconsistencies.append(
                                f"Participant {participant.wallet_address} tickets mismatch: "
                                f"DB={participant.tickets_count}, Blockchain={blockchain_tickets}"
                            )
                            
                except Exception as e:
                    inconsistencies.append(f"Participant {participant.wallet_address} validation error: {e}")
                    
        except Exception as e:
            inconsistencies.append(f"Participants validation error: {e}")

        # 🔹 PRODUCTION: Vérifier les loteries récentes
        try:
            recent_lotteries = Lottery.objects.filter(
                status='completed',
                executed_time__gte=timezone.now() - timedelta(hours=24)
            )
            
            for lottery in recent_lotteries:
                try:
                    # Vérifier si la loterie existe sur la blockchain
                    draw_info = run_async_task(solana_service.get_draw_info(lottery))
                    if not draw_info:
                        inconsistencies.append(f"Lottery {lottery.id} not found on blockchain")
                        
                except Exception as e:
                    inconsistencies.append(f"Lottery {lottery.id} validation error: {e}")
                    
        except Exception as e:
            inconsistencies.append(f"Lotteries validation error: {e}")

        # Log des incohérences
        if inconsistencies:
            logger.error(f"PRODUCTION: Blockchain inconsistencies found: {inconsistencies}")
        else:
            logger.info("PRODUCTION: Blockchain consistency check passed")

        return {
            'timestamp': timezone.now().isoformat(),
            'inconsistencies_count': len(inconsistencies),
            'inconsistencies': inconsistencies,
            'status': 'FAILED' if inconsistencies else 'PASSED'
        }
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR validating blockchain consistency: {e}")
        raise

@shared_task
def emergency_system_check():
    """Vérification d'urgence du système en cas de problème critique"""
    try:
        critical_issues = []
        
        # 🔹 PRODUCTION: Vérifier les tirages bloqués
        stuck_lotteries = Lottery.objects.filter(
            status='pending',
            scheduled_time__lt=timezone.now() - timedelta(hours=1)
        )
        
        if stuck_lotteries.exists():
            critical_issues.append(f"{stuck_lotteries.count()} lotteries stuck for over 1 hour")
            
            # Tenter de débloquer automatiquement
            for lottery in stuck_lotteries[:5]:  # Limiter à 5 pour éviter la surcharge
                try:
                    logger.warning(f"PRODUCTION: Attempting to recover stuck lottery {lottery.id}")
                    # Marquer comme échoué pour investigation manuelle
                    lottery.status = 'failed'
                    lottery.save()
                    
                    AuditLog.objects.create(
                        action_type='emergency_recovery',
                        description=f'PRODUCTION: Lottery {lottery.id} marked as failed due to being stuck',
                        lottery=lottery,
                        metadata={'reason': 'stuck_lottery', 'stuck_duration_hours': 1}
                    )
                    
                except Exception as e:
                    logger.error(f"PRODUCTION ERROR recovering lottery {lottery.id}: {e}")

        # 🔹 PRODUCTION: Vérifier les paiements bloqués
        stuck_payouts = Winner.objects.filter(
            payout_status='pending',
            created_at__lt=timezone.now() - timedelta(hours=2)
        )
        
        if stuck_payouts.exists():
            critical_issues.append(f"{stuck_payouts.count()} payouts stuck for over 2 hours")

        # 🔹 PRODUCTION: Vérifier la connectivité Solana
        try:
            connection_ok = run_async_task(solana_service.check_connection())
            if not connection_ok:
                critical_issues.append("Solana connection failed")
        except Exception as e:
            critical_issues.append(f"Solana connection error: {e}")

        # 🔹 PRODUCTION: Vérifier les jackpots
        try:
            lottery_state = run_async_task(solana_service.get_lottery_state())
            if lottery_state:
                if lottery_state.get('is_paused', False):
                    critical_issues.append("Lottery program is paused on blockchain")
                if lottery_state.get('emergency_stop', False):
                    critical_issues.append("Emergency stop is active on blockchain")
            else:
                critical_issues.append("Cannot fetch lottery state from blockchain")
        except Exception as e:
            critical_issues.append(f"Lottery state check error: {e}")

        # 🔹 PRODUCTION: Envoyer alerte si problèmes critiques
        if critical_issues:
            logger.critical(f"PRODUCTION EMERGENCY: Critical issues detected: {critical_issues}")
            
            # Envoyer notification d'urgence
            try:
                from django.core.mail import send_mail
                from django.conf import settings
                
                if hasattr(settings, 'ADMIN_EMAIL') and settings.ADMIN_EMAIL:
                    send_mail(
                        subject='🚨 PRODUCTION EMERGENCY - Lottery System Critical Issues',
                        message=f"""
PRODUCTION EMERGENCY ALERT

Critical issues detected in the lottery system:

{chr(10).join(f"- {issue}" for issue in critical_issues)}

Timestamp: {timezone.now().isoformat()}
Environment: PRODUCTION

Please investigate immediately.
                        """,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[settings.ADMIN_EMAIL],
                        fail_silently=False
                    )
                    
            except Exception as e:
                logger.error(f"PRODUCTION ERROR sending emergency notification: {e}")

        return {
            'timestamp': timezone.now().isoformat(),
            'critical_issues_count': len(critical_issues),
            'critical_issues': critical_issues,
            'status': 'CRITICAL' if critical_issues else 'OK'
        }
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR in emergency system check: {e}")
        raise

@shared_task
def backup_critical_data():
    """Sauvegarde les données critiques pour la production"""
    try:
        from django.core import serializers
        import json
        from django.conf import settings
        import os
        
        backup_data = {
            'timestamp': timezone.now().isoformat(),
            'environment': 'PRODUCTION'
        }
        
        # 🔹 PRODUCTION: Sauvegarder les loteries récentes
        recent_lotteries = Lottery.objects.filter(
            created_at__gte=timezone.now() - timedelta(days=7)
        )
        backup_data['lotteries'] = json.loads(
            serializers.serialize('json', recent_lotteries)
        )
        
        # 🔹 PRODUCTION: Sauvegarder les gagnants récents
        recent_winners = Winner.objects.filter(
            created_at__gte=timezone.now() - timedelta(days=7)
        )
        backup_data['winners'] = json.loads(
            serializers.serialize('json', recent_winners)
        )
        
        # 🔹 PRODUCTION: Sauvegarder les participants actifs
        active_participants = TokenHolding.objects.filter(is_eligible=True)
        backup_data['participants'] = json.loads(
            serializers.serialize('json', active_participants)
        )
        
        # 🔹 PRODUCTION: Sauvegarder l'état des jackpots
        jackpot_pools = JackpotPool.objects.all()
        backup_data['jackpot_pools'] = json.loads(
            serializers.serialize('json', jackpot_pools)
        )
        
        # 🔹 PRODUCTION: Sauvegarder sur disque
        backup_dir = getattr(settings, 'BACKUP_DIR', '/tmp/lottery_backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        backup_filename = f"lottery_backup_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        with open(backup_path, 'w') as f:
            json.dump(backup_data, f, indent=2, default=str)
        
        # 🔹 PRODUCTION: Nettoyer les anciennes sauvegardes (garder 30 jours)
        cutoff_time = timezone.now() - timedelta(days=30)
        for filename in os.listdir(backup_dir):
            if filename.startswith('lottery_backup_') and filename.endswith('.json'):
                file_path = os.path.join(backup_dir, filename)
                file_time = timezone.datetime.fromtimestamp(
                    os.path.getctime(file_path), 
                    tz=timezone.utc
                )
                if file_time < cutoff_time:
                    os.remove(file_path)
        
        logger.info(f"PRODUCTION: Critical data backup completed: {backup_path}")
        
        return {
            'backup_path': backup_path,
            'lotteries_count': len(backup_data['lotteries']),
            'winners_count': len(backup_data['winners']),
            'participants_count': len(backup_data['participants']),
            'timestamp': backup_data['timestamp']
        }
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR backing up critical data: {e}")
        raise

# 🔹 PRODUCTION: Tâche de surveillance continue
@shared_task
def continuous_monitoring():
    """Surveillance continue du système en production"""
    try:
        monitoring_results = {
            'timestamp': timezone.now().isoformat(),
            'checks': {}
        }
        
        # Vérifier la santé générale
        monitoring_results['checks']['health'] = health_check.delay().get(timeout=30)
        
        # Vérifier la cohérence blockchain
        monitoring_results['checks']['blockchain_consistency'] = validate_blockchain_consistency.delay().get(timeout=60)
        
        # Mettre à jour les pools de jackpot
        monitoring_results['checks']['jackpot_update'] = update_jackpot_pools.delay().get(timeout=30)
        
        # Synchroniser quelques participants
        monitoring_results['checks']['participant_sync'] = sync_participant_holdings.delay().get(timeout=120)
        
        # Calculer le score de santé global
        health_score = 100
        for check_name, check_result in monitoring_results['checks'].items():
            if isinstance(check_result, dict):
                if check_result.get('issues') or check_result.get('errors'):
                    health_score -= 20
                if check_result.get('status') == 'FAILED':
                    health_score -= 30
        
        monitoring_results['health_score'] = max(0, health_score)
        monitoring_results['status'] = 'HEALTHY' if health_score >= 80 else 'DEGRADED' if health_score >= 50 else 'CRITICAL'
        
        logger.info(f"PRODUCTION: Continuous monitoring completed - Health Score: {health_score}%")
        
        return monitoring_results
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR in continuous monitoring: {e}")
        return {
            'timestamp': timezone.now().isoformat(),
            'health_score': 0,
            'status': 'CRITICAL',
            'error': str(e)
        }

