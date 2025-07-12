import os
import django

# Configuration Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from base.models import Lottery, LotteryType, LotteryStatus
from django.utils import timezone
from decimal import Decimal
import uuid

def test_lottery_creation():
    """Test de création de loterie"""
    try:
        print("🔍 Test de création de loterie...")
        
        # Créer une loterie simple
        lottery = Lottery(
            lottery_type=LotteryType.HOURLY,
            scheduled_time=timezone.now() + timezone.timedelta(hours=1),
            status=LotteryStatus.PENDING,
            jackpot_amount_sol=Decimal('0.001'),
            jackpot_amount_usd=Decimal('0.10'),
            total_participants=0,
            total_tickets=0
        )
        
        print(f"✅ Objet Lottery créé: {lottery}")
        
        # Sauvegarder
        lottery.save()
        print(f"✅ Lottery sauvegardée avec ID: {lottery.id}")
        
        # Vérifier en base
        saved_lottery = Lottery.objects.get(id=lottery.id)
        print(f"✅ Lottery récupérée: {saved_lottery}")
        
        # Nettoyer
        saved_lottery.delete()
        print("✅ Lottery supprimée")
        
        return True
        
    except Exception as e:
        print(f"❌ Erreur lors de la création: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_lottery_creation()
    if success:
        print("\n🎉 Test réussi ! Le problème ne vient pas du modèle Lottery.")
    else:
        print("\n💥 Test échoué ! Il y a un problème avec le modèle.")
