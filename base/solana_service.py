import asyncio
import json
import logging
import secrets
import hashlib
from pathlib import Path
from decimal import Decimal
from typing import Optional, Dict, List, Any
from datetime import datetime

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.system_program import TransferParams, transfer
from anchorpy import Program, Provider, Wallet, Idl
from anchorpy.error import ProgramError

from django.conf import settings
from django.utils import timezone
from .models import (
    Lottery, Winner, Transaction as TxModel,
    TokenHolding, JackpotPool, LotteryType, AuditLog
)

logger = logging.getLogger(__name__)

class SolanaService:
    def __init__(self):
        # 🔹 PRODUCTION: Utiliser mainnet-beta au lieu de devnet
        self.rpc_url = getattr(settings, 'SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
        self.program_id = Pubkey.from_string(
            getattr(settings, 'SOLANA_PROGRAM_ID', '2wqFWNXDYT2Q71ToNFBqKpV4scKSi1cjMuqVcT2jgruV')
        )
        self.commitment = Commitment(getattr(settings, 'SOLANA_COMMITMENT', 'confirmed'))

        self.admin_keypair: Optional[Keypair] = None
        admin_private_key = getattr(settings, 'SOLANA_ADMIN_PRIVATE_KEY', None)
        if admin_private_key:
            try:
                if isinstance(admin_private_key, str) and admin_private_key.strip().startswith('['):
                    key_array = json.loads(admin_private_key)
                    self.admin_keypair = Keypair.from_bytes(bytes(key_array))
                else:
                    self.admin_keypair = Keypair.from_base58_string(admin_private_key)
            except Exception as e:
                logger.error(f"Erreur lors du chargement de la clé privée admin : {e}")
                raise ValueError("Invalid admin private key for production")
        else:
            raise ValueError("Admin private key required for production")

        self.connection: Optional[AsyncClient] = None
        self.program: Optional[Program] = None

    # 🔹 PRODUCTION: VRF sécurisé au lieu de random
    def _generate_secure_vrf_seed(self, lottery_id: int, participants_count: int) -> int:
        """Génère un seed VRF cryptographiquement sécurisé"""
        try:
            # Utiliser secrets pour la cryptographie
            random_bytes = secrets.token_bytes(32)
            
            # Combiner avec des données déterministes
            lottery_data = f"{lottery_id}:{participants_count}:{timezone.now().timestamp()}"
            combined = random_bytes + lottery_data.encode('utf-8')
            
            # Hash SHA-256 pour uniformité
            hash_result = hashlib.sha256(combined).digest()
            
            # Convertir en entier 64-bit
            vrf_seed = int.from_bytes(hash_result[:8], byteorder='big')
            
            logger.info(f"Generated secure VRF seed: {vrf_seed}")
            return vrf_seed
            
        except Exception as e:
            logger.error(f"Error generating secure VRF seed: {e}")
            raise ValueError("Failed to generate secure VRF seed")

    # 🔹 PRODUCTION: Validation stricte des wallets
    async def _validate_wallet_exists(self, wallet_address: str) -> bool:
        """Valide qu'un wallet existe réellement sur la blockchain"""
        try:
            connection = await self.get_connection()
            pubkey = Pubkey.from_string(wallet_address)
            
            # Vérifier que le compte existe
            account_info = await connection.get_account_info(pubkey)
            if not account_info.value:
                logger.warning(f"Wallet {wallet_address} does not exist on blockchain")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Error validating wallet {wallet_address}: {e}")
            return False

    # 🔹 PRODUCTION: Validation des participants avec blockchain
    async def sync_participant(self, wallet_address: str) -> Optional[TokenHolding]:
        """Synchronise un participant UNIQUEMENT s'il existe sur la blockchain"""
        try:
            # Valider que le wallet existe
            if not await self._validate_wallet_exists(wallet_address):
                logger.error(f"Cannot sync non-existent wallet: {wallet_address}")
                return None

            participant_info = await self.get_participant_info(wallet_address)
            if not participant_info:
                logger.error(f"No participant info found for: {wallet_address}")
                return None

            holding, _created = TokenHolding.objects.update_or_create(
                wallet_address=wallet_address,
                defaults={
                    'balance': Decimal(str(participant_info['ball_balance'])) / Decimal('100000000'),
                    'tickets_count': participant_info['tickets_count'],
                    'is_eligible': participant_info['is_eligible'],
                    'last_updated': timezone.now()
                }
            )
            
            logger.info(f"Successfully synced participant: {wallet_address}")
            return holding

        except Exception as e:
            logger.error(f"Error syncing participant {wallet_address}: {e}")
            raise  # 🔹 PRODUCTION: Lever l'erreur au lieu de la masquer
        
    async def execute_lottery_on_chain(self, lottery: Lottery, winner_wallet: str) -> bool:
        """Exécute une loterie avec validation complète"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                raise ValueError("Program or admin keypair not available")

            # Valider le gagnant
            if not await self._validate_wallet_exists(winner_wallet):
                raise ValueError(f"Winner wallet does not exist: {winner_wallet}")

            winner_pubkey = Pubkey.from_string(winner_wallet)
            
            # Générer VRF sécurisé
            vrf_seed = self._generate_secure_vrf_seed(
                lottery.id, 
                lottery.total_participants
            )
            
            # 🔹 CORRECTION: Utiliser les bons PDAs selon le programme Rust
            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            # 🔹 CORRECTION: Déterminer le type de loterie et le draw_id
            state = await self.get_lottery_state()
            if not state:
                raise ValueError("Cannot fetch lottery state")

            if lottery.lottery_type == LotteryType.HOURLY:
                lottery_type_enum = {"hourly": {}}
                draw_id = state['hourly_draw_count'] + 1
            else:
                lottery_type_enum = {"daily": {}}
                draw_id = state['daily_draw_count'] + 1

            # 🔹 CORRECTION: Créer la loterie d'abord
            lottery_pda, _bump = Pubkey.find_program_address([
                b"lottery",
                b"hourly" if lottery.lottery_type == LotteryType.HOURLY else b"daily",
                draw_id.to_bytes(4, 'little')
            ], self.program_id)

            # Créer la loterie
            create_tx = await program.rpc["create_lottery"](
                lottery_type_enum,
                int(lottery.scheduled_time.timestamp()) if lottery.scheduled_time else int(timezone.now().timestamp()) + 3600,
                ctx=program.ctx(
                    accounts={
                        "lottery": lottery_pda,
                        "lottery_state": lottery_state_pda,
                        "admin": self.admin_keypair.pubkey,
                        "system_program": Pubkey.from_string("11111111111111111111111111111111")
                    },
                    signers=[self.admin_keypair]
                )
            )

            # 🔹 CORRECTION: Obtenir le PDA du participant gagnant
            winner_participant_pda, _bump = Pubkey.find_program_address([
                b"participant",
                bytes(winner_pubkey)
            ], self.program_id)

            # Exécuter la loterie
            execute_tx = await program.rpc["execute_lottery"](
                lottery_type_enum,
                draw_id,
                winner_pubkey,
                vrf_seed,
                f"lottery_execution_{draw_id}_{int(timezone.now().timestamp())}",
                ctx=program.ctx(
                    accounts={
                        "lottery": lottery_pda,
                        "lottery_state": lottery_state_pda,
                        "admin": self.admin_keypair.pubkey,
                        "winner_participant": winner_participant_pda,
                        "system_program": Pubkey.from_string("11111111111111111111111111111111")
                    },
                    signers=[self.admin_keypair]
                )
            )

            # Mettre à jour en base SEULEMENT si succès blockchain
            lottery.status = 'completed'
            lottery.executed_time = timezone.now()
            lottery.transaction_signature = str(execute_tx)
            lottery.random_seed = str(vrf_seed)
            lottery.save()

            # Créer le gagnant
            winner_holding = TokenHolding.objects.get(wallet_address=winner_wallet)
            Winner.objects.create(
                lottery=lottery,
                wallet_address=winner_wallet,
                winning_amount_sol=lottery.jackpot_amount_sol,
                tickets_held=winner_holding.tickets_count,
                payout_status='pending'
            )

            logger.info(f"PRODUCTION: Lottery {lottery.id} executed successfully: {execute_tx}")
            return True

        except Exception as e:
            logger.error(f"PRODUCTION ERROR executing lottery {lottery.id}: {e}")
            raise

    # 🔹 CORRECTION: Paiement avec les bons PDAs
    async def pay_winner_on_chain(self, winner: Winner) -> bool:
        """Paie un gagnant avec validation complète"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                raise ValueError("Program or admin keypair not available")

            # Valider le gagnant
            if not await self._validate_wallet_exists(winner.wallet_address):
                raise ValueError(f"Winner wallet does not exist: {winner.wallet_address}")

            winner_pubkey = Pubkey.from_string(winner.wallet_address)
            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            # 🔹 CORRECTION: Utiliser le bon PDA de loterie
            if winner.lottery.lottery_type == LotteryType.HOURLY:
                lottery_type_enum = {"hourly": {}}
                type_seed = b"hourly"
            else:
                lottery_type_enum = {"daily": {}}
                type_seed = b"daily"

            # Obtenir le draw_id depuis la base de données ou calculer
            draw_id = winner.lottery.id % (2**32)  # Convertir en u32
            
            lottery_pda, _bump = Pubkey.find_program_address([
                b"lottery",
                type_seed,
                draw_id.to_bytes(4, 'little')
            ], self.program_id)

            tx = await program.rpc["pay_winner"](
                lottery_type_enum,
                draw_id,
                ctx=program.ctx(
                    accounts={
                        "lottery": lottery_pda,
                        "lottery_state": lottery_state_pda,
                        "winner": winner_pubkey,
                        "system_program": Pubkey.from_string("11111111111111111111111111111111")
                    },
                    signers=[self.admin_keypair]
                )
            )

            # Mettre à jour SEULEMENT si succès
            winner.payout_status = 'completed'
            winner.payout_time = timezone.now()
            winner.payout_transaction_signature = str(tx)
            winner.save()

            logger.info(f"PRODUCTION: Winner {winner.wallet_address} paid successfully: {tx}")
            return True

        except Exception as e:
            logger.error(f"PRODUCTION ERROR paying winner {winner.wallet_address}: {e}")
            raise

    # 🔹 CORRECTION: Contribution avec la bonne signature
    async def contribute_to_jackpot(self, sol_amount: int, transaction_signature: str = "", source: str = "DirectDeposit") -> bool:
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            # 🔹 CORRECTION: Mapper la source correctement
            source_enum = {
                "RaydiumSwap": {"raydium_swap": {}},
                "DirectDeposit": {"direct_deposit": {}},
                "Treasury": {"treasury": {}}
            }.get(source, {"direct_deposit": {}})

            tx = await program.rpc["contribute_to_jackpot"](
                sol_amount,
                transaction_signature or f"contribution_{int(timezone.now().timestamp())}",
                source_enum,
                ctx=program.ctx(
                    accounts={
                        "lottery_state": lottery_state_pda,
                        "contributor": self.admin_keypair.pubkey
                    },
                    signers=[self.admin_keypair]
                )
            )

            logger.info(f"Contributed {sol_amount} lamports to jackpot: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error contributing to jackpot: {e}")
            return False

    # 🔹 CORRECTION: Initialisation avec la bonne signature
    async def initialize_program(self, ball_token_mint: str) -> bool:
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            ball_mint_pubkey = Pubkey.from_string(ball_token_mint)
            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )
            
            # 🔹 CORRECTION: Utiliser la signature correcte (admin_authority séparé)
            tx = await program.rpc["initialize"](
                ball_mint_pubkey,
                self.admin_keypair.pubkey,  # admin_authority
                ctx=program.ctx(
                    accounts={
                        "lottery_state": lottery_state_pda,
                        "admin": self.admin_keypair.pubkey,
                        "system_program": Pubkey.from_string("11111111111111111111111111111111")
                    },
                    signers=[self.admin_keypair]
                )
            )
            logger.info(f"Program initialized: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error initializing program: {e}")
            return False

# Instance globale du service
solana_service = SolanaService()

   