import django
import os
import sys
from pathlib import Path

# Configuration Django
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()


from django.core.management.base import BaseCommand
from django.conf import settings
import asyncio
import logging
from base.solana_service import SolanaServiceFactory
from base.test_api_endpoint import integrate_optimized_decoder

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Tester le décodeur Solana optimisé'

    def add_arguments(self, parser):
        parser.add_argument(
            '--full-test',
            action='store_true',
            help='Exécuter un test complet',
        )
        parser.add_argument(
            '--test-account',
            type=str,
            help='Tester un compte spécifique (pubkey)',
        )
        parser.add_argument(
            '--sync-db',
            action='store_true',
            help='Synchroniser avec la base de données',
        )
        parser.add_argument(
            '--analyze-accounts',
            action='store_true',
            help='Analyser tous les comptes du programme',
        )

    def handle(self, *args, **options):
        """Point d'entrée de la commande"""
        self.stdout.write(
            self.style.SUCCESS('🚀 Démarrage du test du décodeur Solana optimisé')
        )
        
        # Exécuter les tests de manière asynchrone
        asyncio.run(self.run_async_tests(options))

    async def run_async_tests(self, options):
        """Exécuter les tests asynchrones"""
        try:
            # Initialiser le service
            solana_service = SolanaServiceFactory.get_instance()
            
            # Intégrer le décodeur optimisé
            solana_service = integrate_optimized_decoder(solana_service)
            
            self.stdout.write("✅ Service Solana initialisé avec décodeur optimisé")
            
            # Exécuter les tests selon les options
            if options['full_test']:
                await self.run_full_test(solana_service)
            elif options['test_account']:
                await self.test_specific_account(solana_service, options['test_account'])
            elif options['sync_db']:
                await self.sync_database(solana_service)
            elif options['analyze_accounts']:
                await self.analyze_accounts(solana_service)
            else:
                # Test par défaut
                await self.run_basic_test(solana_service)
                
        except Exception as e:
            
            self.style.ERROR(f'🚨 Erreur lors des tests: {e}')
            
            logger.error(f"Erreur dans test_solana_decoder: {e}")

    async def run_full_test(self, solana_service):
        """Exécuter un test complet"""
        self.stdout.write(
            self.style.WARNING('🔍 Exécution du test complet...')
        )
        
        try:
            results = await solana_service.decoder_command.run_full_test()
            
            # Afficher les résultats
            if results['lottery_state']:
                self.stdout.write(
                    self.style.SUCCESS('✅ État de la loterie récupéré')
                )
            else:
                self.stdout.write(
                    self.style.ERROR('❌ Échec récupération état loterie')
                )
            
            self.stdout.write(
                f"👥 Participants trouvés: {len(results['participants'])}"
            )
            self.stdout.write(
                f"🎰 Lotteries trouvées: {len(results['lotteries'])}"
            )
            
            if results['errors']:
                self.stdout.write(
                    self.style.ERROR(f"🚨 Erreurs: {len(results['errors'])}")
                )
                for error in results['errors'][:3]:  # Afficher les 3 premières
                    self.stdout.write(f"   • {error}")
            
            # Statistiques
            stats = results.get('statistics', {})
            self.stdout.write(
                f"📊 Taux de succès: {stats.get('success_rate', 0):.1f}%"
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Erreur test complet: {e}')
            )

    async def test_specific_account(self, solana_service, pubkey_str):
        """Tester un compte spécifique"""
        self.stdout.write(
            self.style.WARNING(f'🔍 Test du compte: {pubkey_str}')
        )
        
        try:
            result = await solana_service.decoder_command.test_specific_account(pubkey_str)
            
            if 'error' in result:
                self.stdout.write(
                    self.style.ERROR(f"❌ Erreur: {result['error']}")
                )
                return
            
            self.stdout.write(f"📍 Pubkey: {result['pubkey']}")
            self.stdout.write(f"👤 Owner: {result['owner']}")
            self.stdout.write(f"💰 Lamports: {result['lamports']:,}")
            self.stdout.write(f"📏 Taille données: {result['data_size']} bytes")
            
            if result['success']:
                self.stdout.write(
                    self.style.SUCCESS("✅ Décodage réussi")
                )
                decoded = result['decoded_data']
                
                # Afficher les données décodées selon le type
                if 'admin' in decoded:  # LotteryState
                    self.stdout.write("🏛️ Type: LotteryState")
                    self.stdout.write(f"   👤 Admin: {decoded['admin']}")
                    self.stdout.write(f"   💰 Jackpot horaire: {decoded['hourly_jackpot_sol']:.4f} SOL")
                    self.stdout.write(f"   💎 Jackpot journalier: {decoded['daily_jackpot_sol']:.4f} SOL")
                    
                elif 'wallet' in decoded and 'ball_balance' in decoded:  # Participant
                    self.stdout.write("👤 Type: Participant")
                    self.stdout.write(f"   🏠 Wallet: {decoded['wallet']}")
                    self.stdout.write(f"   🪙 Balance BALL: {decoded['ball_balance_formatted']:.2f}")
                    self.stdout.write(f"   🎫 Tickets: {decoded['tickets_count']}")
                    self.stdout.write(f"   ✅ Éligible: {decoded['is_eligible']}")
                    
                elif 'draw_id' in decoded:  # Lottery
                    self.stdout.write("🎰 Type: Lottery")
                    self.stdout.write(f"   🆔 Draw ID: {decoded['draw_id']}")
                    self.stdout.write(f"   📅 Type: {decoded['lottery_type']}")
                    self.stdout.write(f"   📊 Statut: {decoded['status']}")
                    self.stdout.write(f"   💰 Jackpot: {decoded['jackpot_amount_sol']:.4f} SOL")
                    
            else:
                self.stdout.write(
                    self.style.ERROR("❌ Échec du décodage")
                )
                
                # Afficher l'analyse brute
                raw = result['raw_analysis']
                self.stdout.write(f"🔍 Discriminator: {raw.get('discriminator', 'N/A')}")
                self.stdout.write(f"📏 Taille données compte: {raw.get('account_data_size', 0)}")
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Erreur test compte: {e}')
            )

    async def sync_database(self, solana_service):
        """Synchroniser avec la base de données"""
        self.stdout.write(
            self.style.WARNING('🔄 Synchronisation avec la base de données...')
        )
        
        try:
            results = await solana_service.sync_with_database_optimized()
            
            if results.get('lottery_state_synced'):
                self.stdout.write(
                    self.style.SUCCESS('✅ État de la loterie synchronisé')
                )
            else:
                self.stdout.write(
                    self.style.ERROR('❌ Échec synchronisation état')
                )
            
            synced = results.get('participants_synced', 0)
            failed = results.get('participants_failed', 0)
            
            self.stdout.write(f"👥 Participants synchronisés: {synced}")
            if failed > 0:
                self.stdout.write(
                    self.style.WARNING(f"⚠️ Participants échoués: {failed}")
                )
            
            errors = results.get('errors', [])
            if errors:
                self.stdout.write(
                    self.style.ERROR(f"🚨 Erreurs ({len(errors)}):")
                )
                for error in errors[:5]:  # Afficher les 5 premières
                    self.stdout.write(f"   • {error}")
                    
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Erreur synchronisation: {e}')
            )

    async def analyze_accounts(self, solana_service):
        """Analyser tous les comptes du programme"""
        self.stdout.write(
            self.style.WARNING('🔍 Analyse de tous les comptes du programme...')
        )
        
        try:
            analysis = await solana_service.analyze_all_program_accounts()
            
            # Statistiques générales
            lottery_states = analysis.get('lottery_state', [])
            participants = analysis.get('participant', [])
            lotteries = analysis.get('lottery', [])
            unknown = analysis.get('unknown', [])
            
            self.stdout.write("📊 Résultats de l'analyse:")
            self.stdout.write(f"   🏛️ LotteryState: {len(lottery_states)}")
            self.stdout.write(f"   👥 Participants: {len(participants)}")
            self.stdout.write(f"   🎰 Lotteries: {len(lotteries)}")
            self.stdout.write(f"   ❓ Inconnus: {len(unknown)}")
            
            # Détails des participants
            if participants:
                self.stdout.write("\n👥 Détails des participants:")
                eligible_count = sum(1 for p in participants if p.get('is_eligible', False))
                total_tickets = sum(p.get('tickets_count', 0) for p in participants)
                
                self.stdout.write(f"   ✅ Éligibles: {eligible_count}/{len(participants)}")
                self.stdout.write(f"   🎫 Total tickets: {total_tickets:,}")
                
                # Top 5 participants par tickets
                top_participants = sorted(
                    participants, 
                    key=lambda p: p.get('tickets_count', 0), 
                    reverse=True
                )[:5]
                
                self.stdout.write("   🏆 Top 5 participants:")
                for i, p in enumerate(top_participants, 1):
                    wallet = p.get('wallet', 'N/A')
                    tickets = p.get('tickets_count', 0)
                    self.stdout.write(f"      {i}. {wallet[:8]}... - {tickets:,} tickets")
            
            # Détails des lotteries
            if lotteries:
                self.stdout.write("\n🎰 Détails des lotteries:")
                
                # Grouper par statut
                status_counts = {}
                for lottery in lotteries:
                    status = lottery.get('status', 'Unknown')
                    status_counts[status] = status_counts.get(status, 0) + 1
                
                for status, count in status_counts.items():
                    self.stdout.write(f"   📊 {status}: {count}")
                
                # Dernières lotteries
                recent_lotteries = sorted(
                    lotteries,
                    key=lambda l: l.get('created_at', 0),
                    reverse=True
                )[:3]
                
                self.stdout.write("   🕐 Lotteries récentes:")
                for lottery in recent_lotteries:
                    draw_id = lottery.get('draw_id', 'N/A')
                    lottery_type = lottery.get('lottery_type', 'N/A')
                    status = lottery.get('status', 'N/A')
                    jackpot = lottery.get('jackpot_amount_sol', 0)
                    self.stdout.write(f"      • ID {draw_id} ({lottery_type}) - {status} - {jackpot:.4f} SOL")
            
            # Comptes inconnus
            if unknown:
                self.stdout.write(f"\n❓ Comptes inconnus ({len(unknown)}):")
                for account in unknown[:3]:  # Afficher les 3 premiers
                    pubkey = account.get('pubkey', 'N/A')
                    size = account.get('raw_analysis', {}).get('total_size', 0)
                    self.stdout.write(f"   • {pubkey[:8]}... - {size} bytes")
                    
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Erreur analyse comptes: {e}')
            )

    async def run_basic_test(self, solana_service):
        """Exécuter un test basique"""
        self.stdout.write(
            self.style.WARNING('🔍 Exécution du test basique...')
        )
        
        try:
            # Test 1: État de la loterie
            self.stdout.write("1️⃣ Test état de la loterie...")
            state = await solana_service.get_lottery_state_optimized()
            
            if state:
                self.stdout.write(
                    self.style.SUCCESS("✅ État récupéré avec succès")
                )
                self.stdout.write(f"   👥 Participants: {state.get('total_participants', 0)}")
                self.stdout.write(f"   🎫 Tickets: {state.get('total_tickets', 0)}")
                self.stdout.write(f"   💰 Jackpot horaire: {state.get('hourly_jackpot_sol', 0):.4f} SOL")
                self.stdout.write(f"   💎 Jackpot journalier: {state.get('daily_jackpot_sol', 0):.4f} SOL")
            else:
                self.stdout.write(
                    self.style.ERROR("❌ Échec récupération état")
                )
            
            # Test 2: Vérification de santé
            self.stdout.write("\n2️⃣ Test de santé du service...")
            health = await solana_service.health_check()
            
            if health.get('healthy', False):
                self.stdout.write(
                    self.style.SUCCESS("✅ Service en bonne santé")
                )
            else:
                self.stdout.write(
                    self.style.WARNING("⚠️ Problèmes détectés")
                )
                if 'error' in health:
                    self.stdout.write(f"   Erreur: {health['error']}")
            
            self.stdout.write(
                self.style.SUCCESS('🎉 Test basique terminé')
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Erreur test basique: {e}')
            )

            
           