#!/usr/bin/env python3
"""
🎰 CRÉATION DE PARTICIPANTS POUR DÉMONSTRATION
=============================================

Crée deux participants avec des balances différentes et simule une loterie.

Usage:
    python create_participants_demo.py
    python create_participants_demo.py --play
    python create_participants_demo.py --cleanup
"""

import os
import sys
from pathlib import Path
import django
import time
import random
from decimal import Decimal
from datetime import datetime, timedelta

# Configuration Django
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

try:
    django.setup()
    print("✅ Django configuré avec core.settings")
except Exception as e:
    print(f"❌ Erreur Django: {e}")
    sys.exit(1)

from django.utils import timezone
from django.db import transaction

# Imports conditionnels
try:
    from solders.keypair import Keypair
    SOLANA_AVAILABLE = True
except ImportError:
    print("⚠️  Solders non disponible - utilisation de mocks")
    SOLANA_AVAILABLE = False
    
    class Keypair:
        def __init__(self):
            self._pubkey = f"mock_wallet_{random.randint(10000, 99999)}"
        
        def pubkey(self):
            return self._pubkey

try:
    from base.models import User, TokenHolding, Lottery, Winner, LotteryType
    MODELS_AVAILABLE = True
except ImportError as e:
    print(f"❌ Modèles non disponibles: {e}")
    sys.exit(1)

class ParticipantCreator:
    """Créateur de participants pour la démonstration"""
    
    def __init__(self):
        self.participants = []
        self.lottery = None
    
    def create_participant(self, name, balance_ball, description=""):
        """Créer un participant avec un solde BALL spécifique"""
        try:
            # Générer une adresse de wallet
            keypair = Keypair()
            wallet_address = str(keypair.pubkey() if hasattr(keypair, 'pubkey') else keypair._pubkey)
            
            # Créer l'utilisateur
            user = User.objects.create(
                username=f'demo_{name}_{int(time.time())}',
                email=f'{name}@demo.com',
                wallet_address=wallet_address,
                first_name=name.title(),
                last_name="Demo"
            )
            
            # Calculer les tickets (1 ticket = 10,000 BALL)
            tickets_count = int(balance_ball // 10000)
            is_eligible = tickets_count >= 1
            
            # Créer le holding
            holding = TokenHolding.objects.create(
                wallet_address=wallet_address,
                balance=Decimal(str(balance_ball)),
                tickets_count=tickets_count,
                is_eligible=is_eligible,
                last_updated=timezone.now()
            )
            
            participant_data = {
                'user': user,
                'holding': holding,
                'name': name,
                'description': description,
                'balance_ball': balance_ball,
                'tickets': tickets_count,
                'wallet_address': wallet_address
            }
            
            self.participants.append(participant_data)
            
            print(f"✅ Participant créé: {name}")
            print(f"   💰 Balance: {balance_ball:,.0f} BALL")
            print(f"   🎫 Tickets: {tickets_count}")
            print(f"   ✅ Éligible: {'Oui' if is_eligible else 'Non'}")
            print(f"   🔑 Wallet: {wallet_address[:8]}...{wallet_address[-8:]}")
            print(f"   📝 {description}")
            print()
            
            return participant_data
            
        except Exception as e:
            print(f"❌ Erreur création participant {name}: {e}")
            return None
    
    def create_demo_participants(self):
        """Créer les deux participants de démonstration"""
        print("🎭 CRÉATION DES PARTICIPANTS DE DÉMONSTRATION")
        print("=" * 50)
        
        # Participant 1: Petit joueur
        self.create_participant(
            name="alice",
            balance_ball=25000,  # 2.5 tickets
            description="Petite joueuse avec 2 tickets"
        )
        
        # Participant 2: Gros joueur
        self.create_participant(
            name="bob",
            balance_ball=75000,  # 7.5 tickets
            description="Gros joueur avec 7 tickets"
        )
        
        print(f"🎉 {len(self.participants)} participants créés avec succès!")
        return self.participants
    
    def create_lottery(self, lottery_type=LotteryType.HOURLY, jackpot_amount=5.0):
        """Créer une loterie de démonstration"""
        try:
            total_participants = len([p for p in self.participants if p['holding'].is_eligible])
            total_tickets = sum(p['tickets'] for p in self.participants if p['holding'].is_eligible)
            
            self.lottery = Lottery.objects.create(
                lottery_type=lottery_type,
                scheduled_time=timezone.now() + timedelta(minutes=1),
                jackpot_amount_sol=Decimal(str(jackpot_amount)),
                status='pending',
                total_participants=total_participants,
                total_tickets=total_tickets
            )
            
            print("🎰 LOTERIE CRÉÉE")
            print("=" * 30)
            print(f"🆔 ID: {self.lottery.id}")
            print(f"🎯 Type: {lottery_type}")
            print(f"💰 Jackpot: {jackpot_amount} SOL")
            print(f"👥 Participants: {total_participants}")
            print(f"🎫 Total tickets: {total_tickets}")
            print(f"📅 Programmée: {self.lottery.scheduled_time}")
            print()
            
            return self.lottery
            
        except Exception as e:
            print(f"❌ Erreur création loterie: {e}")
            return None
    
    def select_winner(self):
        """Sélectionner un gagnant basé sur la probabilité des tickets"""
        if not self.lottery:
            print("❌ Aucune loterie créée")
            return None
        
        eligible_participants = [p for p in self.participants if p['holding'].is_eligible]
        
        if not eligible_participants:
            print("❌ Aucun participant éligible")
            return None
        
        # Créer une liste pondérée basée sur les tickets
        weighted_list = []
        for participant in eligible_participants:
            tickets = participant['tickets']
            # Ajouter le participant autant de fois qu'il a de tickets
            weighted_list.extend([participant] * tickets)
        
        # Sélectionner aléatoirement
        winner = random.choice(weighted_list)
        
        print("🎲 SÉLECTION DU GAGNANT")
        print("=" * 30)
        print("📊 Probabilités:")
        total_tickets = sum(p['tickets'] for p in eligible_participants)
        for p in eligible_participants:
            probability = (p['tickets'] / total_tickets) * 100
            print(f"   {p['name']}: {p['tickets']} tickets ({probability:.1f}%)")
        
        print(f"\n🏆 GAGNANT: {winner['name'].upper()}")
        print(f"🎫 Tickets gagnants: {winner['tickets']}")
        print(f"💰 Gain: {self.lottery.jackpot_amount_sol} SOL")
        print()
        
        return winner
    
    def execute_lottery(self):
        """Exécuter la loterie complète"""
        if not self.lottery:
            print("❌ Aucune loterie à exécuter")
            return False
        
        try:
            with transaction.atomic():
                # Sélectionner le gagnant
                winner_data = self.select_winner()
                if not winner_data:
                    return False
                
                # Mettre à jour la loterie
                self.lottery.status = 'completed'
                self.lottery.executed_time = timezone.now()
                self.lottery.save()
                
                # Créer l'enregistrement du gagnant
                winner = Winner.objects.create(
                    lottery=self.lottery,
                    wallet_address=winner_data['wallet_address'],
                    winning_amount_sol=self.lottery.jackpot_amount_sol,
                    tickets_held=winner_data['tickets'],
                    payout_status='completed',
                    payout_time=timezone.now()
                )
                
                print("✅ LOTERIE EXÉCUTÉE AVEC SUCCÈS!")
                print(f"🆔 Loterie ID: {self.lottery.id}")
                print(f"🏆 Gagnant: {winner_data['name']}")
                print(f"💸 Montant payé: {winner.winning_amount_sol} SOL")
                print()
                
                return True
                
        except Exception as e:
            print(f"❌ Erreur exécution loterie: {e}")
            return False
    
    def show_summary(self):
        """Afficher un résumé de la démonstration"""
        print("📊 RÉSUMÉ DE LA DÉMONSTRATION")
        print("=" * 40)
        
        print("👥 PARTICIPANTS:")
        for p in self.participants:
            status = "🏆 GAGNANT" if self.lottery and hasattr(self.lottery, 'winner') else "👤 Participant"
            print(f"   {status} {p['name']}: {p['balance_ball']:,} BALL → {p['tickets']} tickets")
        
        if self.lottery:
            print(f"\n🎰 LOTERIE:")
            print(f"   ID: {self.lottery.id}")
            print(f"   Statut: {self.lottery.status}")
            print(f"   Jackpot: {self.lottery.jackpot_amount_sol} SOL")
            
            if self.lottery.status == 'completed':
                try:
                    winner = Winner.objects.get(lottery=self.lottery)
                    winner_name = next(p['name'] for p in self.participants if p['wallet_address'] == winner.wallet_address)
                    print(f"   🏆 Gagnant: {winner_name}")
                    print(f"   💰 Montant: {winner.winning_amount_sol} SOL")
                except Winner.DoesNotExist:
                    pass
        
        print()
    
    def cleanup(self):
        """Nettoyer les données de démonstration"""
        try:
            # Supprimer les gagnants
            if self.lottery:
                Winner.objects.filter(lottery=self.lottery).delete()
                self.lottery.delete()
            
            # Supprimer les participants
            for p in self.participants:
                p['holding'].delete()
                p['user'].delete()
            
            print("🧹 Nettoyage terminé")
            
        except Exception as e:
            print(f"⚠️  Erreur nettoyage: {e}")


def main():
    """Fonction principale"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Démonstration loterie avec participants')
    parser.add_argument('--play', action='store_true', help='Créer et jouer une loterie complète')
    parser.add_argument('--cleanup', action='store_true', help='Nettoyer les données de démonstration')
    parser.add_argument('--jackpot', type=float, default=5.0, help='Montant du jackpot en SOL')
    
    args = parser.parse_args()
    
    creator = ParticipantCreator()
    
    try:
        if args.cleanup:
            print("🧹 NETTOYAGE DES DONNÉES DE DÉMONSTRATION")
            print("=" * 50)
            
            # Nettoyer toutes les données de démonstration
            User.objects.filter(username__startswith='demo_').delete()
            TokenHolding.objects.filter(wallet_address__contains='mock_wallet').delete()
            Lottery.objects.filter(status__in=['pending', 'completed']).delete()
            
            print("✅ Nettoyage terminé")
            return
        
        if args.play:
            print("🎰 DÉMONSTRATION COMPLÈTE DE LOTERIE")
            print("=" * 50)
            
            # 1. Créer les participants
            participants = creator.create_demo_participants()
            
            # 2. Créer la loterie
            lottery = creator.create_lottery(jackpot_amount=args.jackpot)
            
            if lottery:
                # 3. Attendre un peu pour le suspense
                print("⏳ Préparation du tirage...")
                time.sleep(2)
                
                # 4. Exécuter la loterie
                success = creator.execute_lottery()
                
                if success:
                    # 5. Afficher le résumé
                    creator.show_summary()
                    
                    print("🎉 DÉMONSTRATION TERMINÉE AVEC SUCCÈS!")
                    print("💡 Utilisez --cleanup pour nettoyer les données")
                else:
                    print("❌ Échec de l'exécution de la loterie")
            else:
                print("❌ Échec de la création de la loterie")
        
        else:
            # Mode simple: créer seulement les participants
            print("👥 CRÉATION DE PARTICIPANTS SEULEMENT")
            print("=" * 40)
            
            participants = creator.create_demo_participants()
            creator.show_summary()
            
            print("💡 Utilisez --play pour jouer une loterie complète")
            print("💡 Utilisez --cleanup pour nettoyer les données")
    
    except KeyboardInterrupt:
        print("\n⏹️  Démonstration interrompue")
        creator.cleanup()
    
    except Exception as e:
        print(f"💥 Erreur: {e}")
        creator.cleanup()


if __name__ == "__main__":
    main()
