from django.core.management.base import BaseCommand
from django.utils import timezone
import asyncio
from base.solana_service import solana_service
from base.models import TokenHolding

class Command(BaseCommand):
    help = 'Synchronise les données avec Solana'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--participants',
            action='store_true',
            help='Synchroniser tous les participants'
        )
        parser.add_argument(
            '--state',
            action='store_true',
            help='Synchroniser l\'état de la loterie'
        )
        parser.add_argument(
            '--wallet',
            type=str,
            help='Synchroniser un wallet spécifique'
        )
    
    def handle(self, *args, **options):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            if options['state']:
                self.stdout.write('Synchronisation de l\'état de la loterie...')
                result = loop.run_until_complete(solana_service.sync_lottery_state())
                if result:
                    self.stdout.write(
                        self.style.SUCCESS(f'État synchronisé: {result}')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR('Échec de la synchronisation de l\'état')
                    )
            
            if options['wallet']:
                self.stdout.write(f'Synchronisation du wallet {options["wallet"]}...')
                result = loop.run_until_complete(
                    solana_service.sync_participant(options['wallet'])
                )
                if result:
                    self.stdout.write(
                        self.style.SUCCESS(f'Wallet synchronisé: {result.wallet_address}')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR('Échec de la synchronisation du wallet')
                    )
            
            if options['participants']:
                self.stdout.write('Synchronisation de tous les participants...')
                participants = TokenHolding.objects.all()
                
                synced = 0
                for participant in participants:
                    try:
                        result = loop.run_until_complete(
                            solana_service.sync_participant(participant.wallet_address)
                        )
                        if result:
                            synced += 1
                            self.stdout.write(f'✓ {participant.wallet_address}')
                        else:
                            self.stdout.write(f'✗ {participant.wallet_address}')
                    except Exception as e:
                        self.stdout.write(f'✗ {participant.wallet_address}: {e}')
                
                self.stdout.write(
                    self.style.SUCCESS(f'Synchronisé {synced}/{participants.count()} participants')
                )
            
            if not any([options['state'], options['wallet'], options['participants']]):
                self.stdout.write(
                    self.style.WARNING('Aucune option spécifiée. Utilisez --help pour voir les options.')
                )
        
        finally:
            loop.close()