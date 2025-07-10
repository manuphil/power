from django.core.management.base import BaseCommand
import asyncio
from base.models import Winner
from base.solana_service import solana_service

class Command(BaseCommand):
    help = 'Paye les gagnants en attente'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--wallet',
            type=str,
            help='Payer un wallet spécifique'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Payer tous les gagnants en attente'
        )
    
    def handle(self, *args, **options):
        wallet = options.get('wallet')
        pay_all = options.get('all')
        
        if wallet:
            # Payer un wallet spécifique
            try:
                winner = Winner.objects.get(
                    wallet_address=wallet,
                    payout_status='pending'
                )
                self.pay_winner(winner)
            except Winner.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'Aucun gagnant en attente pour le wallet {wallet}')
                )
        
        elif pay_all:
            # Payer tous les gagnants en attente
            pending_winners = Winner.objects.filter(payout_status='pending')
            
            if not pending_winners.exists():
                self.stdout.write(
                    self.style.SUCCESS('Aucun gagnant en attente de paiement')
                )
                return
            
            self.stdout.write(f'Paiement de {pending_winners.count()} gagnants...')
            
            for winner in pending_winners:
                self.pay_winner(winner)
        
        else:
            self.stdout.write(
                self.style.WARNING('Utilisez --wallet <address> ou --all')
            )
    
    def pay_winner(self, winner):
        """Paye un gagnant spécifique"""
        self.stdout.write(f'Paiement de {winner.wallet_address}...')
        self.stdout.write(f'  Montant: {winner.winning_amount_sol} SOL')
        self.stdout.write(f'  Tirage: {winner.lottery.id}')
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            success = loop.run_until_complete(
                solana_service.pay_winner_on_chain(winner)
            )
            
            if success:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ {winner.wallet_address} payé avec succès!')
                )
                self.stdout.write(f'  Transaction: {winner.payout_transaction_signature}')
            else:
                self.stdout.write(
                    self.style.ERROR(f'✗ Échec du paiement pour {winner.wallet_address}')
                )
        
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Erreur lors du paiement de {winner.wallet_address}: {e}')
            )
        
        finally:
            loop.close()