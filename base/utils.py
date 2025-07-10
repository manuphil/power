
# 🔹 PRODUCTION: Utilitaires pour les tâches
def log_task_execution(task_name, result, execution_time=None):
    """Log l'exécution des tâches pour le monitoring"""
    try:
        AuditLog.objects.create(
            action_type='task_execution',
            description=f'PRODUCTION: Task {task_name} executed',
            metadata={
                'task_name': task_name,
                'result': str(result)[:1000],  # Limiter la taille
                'execution_time': execution_time,
                'timestamp': timezone.now().isoformat()
            }
        )
    except Exception as e:
        logger.error(f"PRODUCTION ERROR logging task execution: {e}")

def get_system_metrics():
    """Récupère les métriques système pour le monitoring"""
    try:
        import psutil
        
        return {
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent,
            'timestamp': timezone.now().isoformat()
        }
    except ImportError:
        return {'error': 'psutil not installed'}
    except Exception as e:
        return {'error': str(e)}

# 🔹 PRODUCTION: Tâche de diagnostic avancé
@shared_task
def advanced_diagnostics():
    """Diagnostic avancé du système pour la production"""
    try:
        diagnostics = {
            'timestamp': timezone.now().isoformat(),
            'environment': 'PRODUCTION',
            'system_metrics': get_system_metrics(),
            'database_stats': {},
            'blockchain_stats': {},
            'performance_metrics': {}
        }
        
        # 🔹 Statistiques de base de données
        try:
            diagnostics['database_stats'] = {
                'total_lotteries': Lottery.objects.count(),
                'pending_lotteries': Lottery.objects.filter(status='pending').count(),
                'completed_lotteries': Lottery.objects.filter(status='completed').count(),
                'failed_lotteries': Lottery.objects.filter(status='failed').count(),
                'total_winners': Winner.objects.count(),
                'pending_payouts': Winner.objects.filter(payout_status='pending').count(),
                'completed_payouts': Winner.objects.filter(payout_status='completed').count(),
                'active_participants': TokenHolding.objects.filter(is_eligible=True).count(),
                'total_participants': TokenHolding.objects.count(),
                'total_transactions': Transaction.objects.count(),
                'audit_logs_count': AuditLog.objects.count()
            }
        except Exception as e:
            diagnostics['database_stats']['error'] = str(e)
        
        # 🔹 Statistiques blockchain
        try:
            lottery_state = run_async_task(solana_service.get_lottery_state())
            if lottery_state:
                diagnostics['blockchain_stats'] = {
                    'hourly_jackpot_lamports': lottery_state['hourly_jackpot'],
                    'daily_jackpot_lamports': lottery_state['daily_jackpot'],
                    'hourly_jackpot_sol': str(Decimal(str(lottery_state['hourly_jackpot'])) / Decimal('1000000000')),
                    'daily_jackpot_sol': str(Decimal(str(lottery_state['daily_jackpot'])) / Decimal('1000000000')),
                    'total_participants': lottery_state['total_participants'],
                    'total_tickets': lottery_state['total_tickets'],
                    'hourly_draw_count': lottery_state['hourly_draw_count'],
                    'daily_draw_count': lottery_state['daily_draw_count'],
                    'is_paused': lottery_state['is_paused'],
                    'last_hourly_draw': lottery_state['last_hourly_draw'],
                    'last_daily_draw': lottery_state['last_daily_draw']
                }
            else:
                diagnostics['blockchain_stats']['error'] = 'Cannot fetch lottery state'
        except Exception as e:
            diagnostics['blockchain_stats']['error'] = str(e)
        
        # 🔹 Métriques de performance
        try:
            now = timezone.now()
            last_hour = now - timedelta(hours=1)
            last_day = now - timedelta(days=1)
            
            diagnostics['performance_metrics'] = {
                'lotteries_last_hour': Lottery.objects.filter(
                    executed_time__gte=last_hour
                ).count(),
                'lotteries_last_day': Lottery.objects.filter(
                    executed_time__gte=last_day
                ).count(),
                'payouts_last_hour': Winner.objects.filter(
                    payout_time__gte=last_hour
                ).count(),
                'payouts_last_day': Winner.objects.filter(
                    payout_time__gte=last_day
                ).count(),
                'transactions_last_hour': Transaction.objects.filter(
                    block_time__gte=last_hour
                ).count(),
                'transactions_last_day': Transaction.objects.filter(
                    block_time__gte=last_day
                ).count(),
                'avg_lottery_execution_time': 'N/A',  # À implémenter si nécessaire
                'avg_payout_time': 'N/A'  # À implémenter si nécessaire
            }
        except Exception as e:
            diagnostics['performance_metrics']['error'] = str(e)
        
        logger.info(f"PRODUCTION: Advanced diagnostics completed")
        return diagnostics
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR in advanced diagnostics: {e}")
        raise

# 🔹 PRODUCTION: Tâche de récupération automatique
@shared_task
def auto_recovery():
    """Récupération automatique en cas de problèmes détectés"""
    try:
        recovery_actions = []
        
        # 🔹 Récupérer les loteries bloquées
        stuck_lotteries = Lottery.objects.filter(
            status='pending',
            scheduled_time__lt=timezone.now() - timedelta(minutes=30)
        )
        
        for lottery in stuck_lotteries[:3]:  # Limiter à 3 pour éviter la surcharge
            try:
                # Vérifier si la loterie peut être exécutée
                eligible_participants = TokenHolding.objects.filter(
                    is_eligible=True,
                    tickets_count__gt=0
                )
                
                if eligible_participants.exists():
                    winner = select_lottery_winner_secure(eligible_participants)
                    if winner:
                        success = run_async_task(
                            solana_service.execute_lottery_on_chain(lottery, winner.wallet_address)
                        )
                        
                        if success:
                            recovery_actions.append(f"Recovered stuck lottery {lottery.id}")
                            logger.info(f"PRODUCTION: Auto-recovered lottery {lottery.id}")
                        else:
                            lottery.status = 'failed'
                            lottery.save()
                            recovery_actions.append(f"Marked lottery {lottery.id} as failed")
                else:
                    lottery.status = 'cancelled'
                    lottery.save()
                    recovery_actions.append(f"Cancelled lottery {lottery.id} - no participants")
                    
            except Exception as e:
                logger.error(f"PRODUCTION ERROR recovering lottery {lottery.id}: {e}")
                recovery_actions.append(f"Failed to recover lottery {lottery.id}: {e}")
        
        # 🔹 Réessayer les paiements échoués
        failed_payouts = Winner.objects.filter(
            payout_status='pending',
            created_at__lt=timezone.now() - timedelta(hours=1)
        )
        
        for winner in failed_payouts[:5]:  # Limiter à 5
            try:
                success = run_async_task(solana_service.pay_winner_on_chain(winner))
                if success:
                    recovery_actions.append(f"Recovered payout for {winner.wallet_address}")
                    logger.info(f"PRODUCTION: Auto-recovered payout for {winner.wallet_address}")
                else:
                    recovery_actions.append(f"Failed to recover payout for {winner.wallet_address}")
                    
            except Exception as e:
                logger.error(f"PRODUCTION ERROR recovering payout for {winner.wallet_address}: {e}")
                recovery_actions.append(f"Error recovering payout for {winner.wallet_address}: {e}")
        
        # 🔹 Synchroniser les participants désynchronisés
        try:
            # Synchroniser quelques participants au hasard
            participants_to_sync = TokenHolding.objects.filter(
                last_updated__lt=timezone.now() - timedelta(hours=2)
            ).order_by('?')[:10]
            
            synced_count = 0
            for participant in participants_to_sync:
                try:
                    result = run_async_task(
                        solana_service.sync_participant(participant.wallet_address)
                    )
                    if result:
                        synced_count += 1
                except Exception:
                    continue
                    
            if synced_count > 0:
                recovery_actions.append(f"Synchronized {synced_count} participants")
                
        except Exception as e:
            recovery_actions.append(f"Participant sync error: {e}")
        
        logger.info(f"PRODUCTION: Auto-recovery completed - Actions: {recovery_actions}")
        
        return {
            'timestamp': timezone.now().isoformat(),
            'actions_taken': recovery_actions,
            'actions_count': len(recovery_actions)
        }
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR in auto-recovery: {e}")
        raise

# 🔹 PRODUCTION: Tâche finale de validation
@shared_task
def final_system_validation():
    """Validation finale du système avant mise en production"""
    try:
        validation_results = {
            'timestamp': timezone.now().isoformat(),
            'environment': 'PRODUCTION',
            'validations': {},
            'overall_status': 'UNKNOWN'
        }
        
        # 🔹 Valider la connexion Solana
        try:
            connection_ok = run_async_task(solana_service.check_connection())
            validation_results['validations']['solana_connection'] = {
                'status': 'PASS' if connection_ok else 'FAIL',
                'details': 'Connection successful' if connection_ok else 'Connection failed'
            }
        except Exception as e:
            validation_results['validations']['solana_connection'] = {
                'status': 'FAIL',
                'details': str(e)
            }
        
        # 🔹 Valider l'état du programme
        try:
            lottery_state = run_async_task(solana_service.get_lottery_state())
            if lottery_state:
                validation_results['validations']['program_state'] = {
                    'status': 'PASS',
                    'details': f"Program active, {lottery_state['total_participants']} participants"
                }
            else:
                validation_results['validations']['program_state'] = {
                    'status': 'FAIL',
                    'details': 'Cannot fetch program state'
                }
        except Exception as e:
            validation_results['validations']['program_state'] = {
                'status': 'FAIL',
                'details': str(e)
            }
        
        # 🔹 Valider la base de données
        try:
            db_count = Lottery.objects.count()
            validation_results['validations']['database'] = {
                'status': 'PASS',
                'details': f'Database accessible, {db_count} lotteries'
            }
        except Exception as e:
            validation_results['validations']['database'] = {
                'status': 'FAIL',
                'details': str(e)
            }
        
        # 🔹 Valider les tâches Celery
        try:
            # Tester une tâche simple
            health_result = health_check.delay().get(timeout=30)
            validation_results['validations']['celery'] = {
                'status': 'PASS',
                'details': 'Celery tasks working'
            }
        except Exception as e:
            validation_results['validations']['celery'] = {
                'status': 'FAIL',
                'details': str(e)
            }
        
        # 🔹 Calculer le statut global
        all_passed = all(
            v['status'] == 'PASS' 
            for v in validation_results['validations'].values()
        )
        
        validation_results['overall_status'] = 'READY_FOR_PRODUCTION' if all_passed else 'NOT_READY'
        
        if all_passed:
            logger.info("PRODUCTION: System validation PASSED - Ready for production!")
        else:
            logger.error(f"PRODUCTION: System validation FAILED - {validation_results}")
        
        return validation_results
        
    except Exception as e:
        logger.error(f"PRODUCTION ERROR in final system validation: {e}")
        return {
            'timestamp': timezone.now().isoformat(),
            'environment': 'PRODUCTION',
            'overall_status': 'VALIDATION_ERROR',
            'error': str(e)
        }

# 🔹 PRODUCTION: Message de fin
logger.info("PRODUCTION: Lottery tasks module loaded successfully")

