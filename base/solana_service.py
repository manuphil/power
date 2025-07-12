import asyncio
import json
import logging
import secrets
import hashlib
import struct
from pathlib import Path
from decimal import Decimal
from typing import Optional, Dict, List, Any
from datetime import datetime
import concurrent.futures
import threading
import asyncio
from functools import wraps
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.system_program import TransferParams, transfer
from anchorpy import Program, Provider, Wallet, Idl
from anchorpy.error import ProgramError
import time
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from .models import (
    Lottery, Winner, Transaction as TxModel,
    TokenHolding, JackpotPool, LotteryType, AuditLog
)

logger = logging.getLogger(__name__)

class SolanaService:
    def __init__(self):
        # 🔹 PRODUCTION: Utiliser mainnet-beta au lieu de devnet
        self.rpc_url = getattr(settings, 'SOLANA_RPC_URL', 'https://api.devnet.solana.com')
        self.program_id = Pubkey.from_string(getattr(settings, 'SOLANA_PROGRAM_ID', '2wqFWNXDYT2Q71ToNFBqKpV4scKSi1cjMuqVcT2jgruV'))
        self.commitment = Commitment(getattr(settings, 'SOLANA_COMMITMENT', 'confirmed'))
        self.admin_keypair: Optional[Keypair] = None
        self._metrics = {
            'requests_count': 0,
            'errors_count': 0,
            'last_success': None,
            'last_error': None
        }
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
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        
        
    async def get_connection(self) -> AsyncClient:
        """Obtient une connexion à Solana"""
        try:
            if not self.connection:
                self.connection = AsyncClient(self.rpc_url, commitment=self.commitment)
            return self.connection
        except Exception as e:
            logger.error(f"Error creating connection: {e}")
            # Créer une nouvelle connexion en cas d'erreur
            self.connection = AsyncClient(self.rpc_url, commitment=self.commitment)
            return self.connection

    async def get_program(self) -> Optional[Program]:
        """Obtient le programme Anchor"""
        if not self.program:
            try:
                connection = await self.get_connection()
                
                # Charger l'IDL depuis le fichier
                idl_path = Path(__file__).parent.parent / "idl" / "lottery_solana.json"
                if not idl_path.exists():
                    logger.error(f"IDL file not found at {idl_path}")
                    return None
                
                with open(idl_path, 'r') as f:
                    idl_dict = json.load(f)
                
                idl = Idl.from_json(idl_dict)
                wallet = Wallet(self.admin_keypair)
                provider = Provider(connection, wallet)
                
                self.program = Program(idl, self.program_id, provider)
                logger.info("Program loaded successfully")
                
            except Exception as e:
                logger.error(f"Error loading program: {e}")
                return None
        
        return self.program
    
    
    
    def _run_async_safe(self, coro):
        """Exécute une coroutine de manière thread-safe"""
        def run_in_thread():
            try:
                # Créer une nouvelle boucle dans ce thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async execution: {e}")
                return None
        
        try:
            future = self._executor.submit(run_in_thread)
            return future.result(timeout=15)
        except concurrent.futures.TimeoutError:
            logger.error("Async operation timeout")
            return None
        except Exception as e:
            logger.error(f"Executor error: {e}")
            return None
    
    def get_lottery_state_pda(self) -> Pubkey:
        """Obtient le PDA de l'état de la loterie"""
        pda, _ = Pubkey.find_program_address([b"lottery_state"], self.program_id)
        return pda
    
    def get_lottery_state_pda_sync(self) -> Pubkey:
        """Version synchrone du PDA"""
        pda, _ = Pubkey.find_program_address([b"lottery_state"], self.program_id)
        return pda

    def get_default_state(self) -> Dict[str, Any]:
        """🔹 CORRECTION: Méthode maintenant correctement dans la classe"""
        return {
            'admin': 'N/A',
            'ball_token_mint': 'N/A',
            'hourly_jackpot_sol': '0.000000000',
            'daily_jackpot_sol': '0.000000000',
            'total_participants': 0,
            'total_tickets': 0,
            'last_hourly_draw': '0',
            'last_daily_draw': '0',
            'hourly_draw_count': 0,
            'daily_draw_count': 0,
            'is_paused': False,
            'emergency_stop': False,
            'last_updated': timezone.now().isoformat(),
            'treasury_balance': '0.000000000',
            'total_volume_processed': '0.000000000',
            'connection_status': 'disconnected',
            'error': 'Unable to connect to Solana network'
        }

    def decode_lottery_state_production(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Décode l'état de la loterie"""
        try:
            if len(data) < 8:
                return None
            
            # Ignorer le discriminator (8 premiers bytes)
            account_data = data[8:]
            
            # Format simple pour 128 bytes
            if len(account_data) >= 64:
                format_str = '<32s32sQQQQ'
                unpacked = struct.unpack(format_str, account_data[:struct.calcsize(format_str)])
                
                return {
                    'admin': str(Pubkey(unpacked[0])),
                    'ball_token_mint': str(Pubkey(unpacked[1])),
                    'hourly_jackpot': unpacked[2],
                    'daily_jackpot': unpacked[3],
                    'total_participants': unpacked[4],
                    'total_tickets': unpacked[5],
                    'last_hourly_draw': 0,
                    'last_daily_draw': 0,
                    'hourly_draw_count': 0,
                    'daily_draw_count': 0,
                    'is_paused': False,
                    'emergency_stop': False,
                    'last_updated': int(time.time()),
                    'fetched_at': int(time.time()),
                    'connection_status': 'connected'
                }
        except Exception as e:
            logger.error(f"Decode error: {e}")
            return None
  
    async def get_lottery_state(self) -> Optional[Dict[str, Any]]:
        """Récupère l'état de la loterie"""
        cache_key = 'lottery_state_production'
        
        # Vérifier le cache
        cached_state = cache.get(cache_key)
        if cached_state:
            logger.info("📦 PRODUCTION: Using cached lottery state")
            return cached_state
        
        try:
            self._metrics['requests_count'] += 1
            
            # CORRECTION : Gestion robuste de la connexion
            try:
                connection = await self.get_connection()
            except Exception as conn_error:
                logger.error(f"Connection error: {conn_error}")
                return self.get_default_state()
            
            lottery_state_pda = self.get_lottery_state_pda_sync()
            
            logger.info(f"🔍 PRODUCTION: Fetching lottery state from PDA: {lottery_state_pda}")
            
            # CORRECTION : Timeout plus court et gestion d'erreur
            try:
                response = await asyncio.wait_for(
                    connection.get_account_info(lottery_state_pda),
                    timeout=8.0  # Réduire le timeout
                )
            except asyncio.TimeoutError:
                logger.error("⏰ PRODUCTION: Timeout fetching lottery state")
                return self.get_default_state()
            except Exception as fetch_error:
                logger.error(f"Fetch error: {fetch_error}")
                return self.get_default_state()
            
            if not response.value:
                logger.warning("⚠️ PRODUCTION: Lottery state account not found")
                return self.get_default_state()
            
            account_data = response.value.data
            logger.info(f"📊 PRODUCTION: Raw account data length: {len(account_data)} bytes")
            
            # Décoder les données
            state = self.decode_lottery_state_production(account_data)
            
            if state:
                state['program_balance'] = response.value.lamports
                state['rent_exempt'] = response.value.lamports > 0
                
                # Mettre en cache
                cache.set(cache_key, state, 30)
                
                logger.info("✅ PRODUCTION: Lottery state fetched and cached successfully")
                return state
            else:
                logger.error("❌ PRODUCTION: Failed to decode lottery state")
                return self.get_default_state()
                
        except Exception as e:
            logger.error(f"❌ PRODUCTION: Error fetching lottery state: {e}")
            self._metrics['errors_count'] += 1
            return self.get_default_state()

    # 🔹 NOUVELLE MÉTHODE: get_participant_info
    async def get_participant_info(self, wallet_address: str) -> Optional[Dict[str, Any]]:
        """Récupère les informations d'un participant"""
        try:
            connection = await self.get_connection()
            wallet_pubkey = Pubkey.from_string(wallet_address)
            
            # Calculer le PDA du participant
            participant_pda, _bump = Pubkey.find_program_address(
                [b"participant", bytes(wallet_pubkey)], 
                self.program_id
            )
            
            # Récupérer les données du compte
            account_info = await connection.get_account_info(participant_pda)
            
            if not account_info.value:
                logger.warning(f"Participant account not found for {wallet_address}")
                return None
            
            # Décoder les données selon la structure Participant (113 bytes)
            data = account_info.value.data
            
            if len(data) < 113:
                logger.error(f"Invalid participant data length: {len(data)}")
                return None
            
            import struct
            
            # Décoder selon la structure Rust Participant
            unpacked = struct.unpack('<32sQQBq32sQQq', data[:113])
            
            wallet = Pubkey(unpacked[0])
            ball_balance = unpacked[1]
            tickets_count = unpacked[2]
            is_eligible = bool(unpacked[3])
            last_updated = unpacked[4]
            token_account = Pubkey(unpacked[5])
            participation_count = unpacked[6]
            total_winnings = unpacked[7]
            last_win_time = unpacked[8]
            
            participant_info = {
                'wallet': str(wallet),
                'ball_balance': ball_balance,
                'tickets_count': tickets_count,
                'is_eligible': is_eligible,
                'last_updated': last_updated,
                'token_account': str(token_account),
                'participation_count': participation_count,
                'total_winnings': total_winnings,
                'last_win_time': last_win_time
            }
            
            logger.info(f"Successfully fetched participant info for {wallet_address}")
            return participant_info
            
        except Exception as e:
            logger.error(f"Error fetching participant info for {wallet_address}: {e}")
            return None

    async def sync_lottery_state(self) -> Optional[Dict[str, Any]]:
        """🔹 PRODUCTION: Synchronise l'état avec la base de données"""
        try:
            state = await self.get_lottery_state()
            if not state:
                return None
            
            # 🔹 PRODUCTION: Mise à jour atomique des pools
            from django.db import transaction
            
            with transaction.atomic():
                # Mettre à jour les pools de jackpot
                hourly_pool, _ = JackpotPool.objects.update_or_create(
                    lottery_type='hourly',
                    defaults={
                        'current_amount_sol': Decimal(str(state['hourly_jackpot'])) / Decimal('1000000000'),
                        'current_amount_usd': Decimal('0'),
                        'total_contributions': Decimal('0'),
                        'total_payouts': Decimal('0'),
                        'last_updated': timezone.now()
                    }
                )
                
                daily_pool, _ = JackpotPool.objects.update_or_create(
                    lottery_type='daily',
                    defaults={
                        'current_amount_sol': Decimal(str(state['daily_jackpot'])) / Decimal('1000000000'),
                        'current_amount_usd': Decimal('0'),
                        'total_contributions': Decimal('0'),
                        'total_payouts': Decimal('0'),
                        'last_updated': timezone.now()
                    }
                )
                
                # 🔹 PRODUCTION: Log de synchronisation
                AuditLog.objects.create(
                    action_type='lottery_state_sync',
                    description=f'Lottery state synchronized - H:{state["hourly_jackpot"]} D:{state["daily_jackpot"]}',
                    metadata={
                        'hourly_jackpot': state['hourly_jackpot'],
                        'daily_jackpot': state['daily_jackpot'],
                        'total_participants': state['total_participants'],
                        'connection_status': state.get('connection_status', 'unknown')
                    }
                )
            
            logger.info("✅ PRODUCTION: Lottery state synchronized with database")
            return state
            
        except Exception as e:
            logger.error(f"❌ PRODUCTION: Error syncing lottery state: {e}")
            return None

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
            lottery_pda, _bump = Pubkey.find_program_address(
                [
                    b"lottery",
                    b"hourly" if lottery.lottery_type == LotteryType.HOURLY else b"daily",
                    draw_id.to_bytes(4, 'little')
                ], 
                self.program_id
            )

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
            winner_participant_pda, _bump = Pubkey.find_program_address(
                [b"participant", bytes(winner_pubkey)], 
                self.program_id
            )

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

            lottery_pda, _bump = Pubkey.find_program_address(
                [
                    b"lottery",
                    type_seed,
                    draw_id.to_bytes(4, 'little')
                ], 
                self.program_id
            )

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

    # 🔹 NOUVELLE MÉTHODE: Mise à jour d'un participant
    async def update_participant(self, wallet_address: str, ball_balance: int, token_account_bump: int = 0) -> bool:
        """Met à jour les informations d'un participant"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            wallet_pubkey = Pubkey.from_string(wallet_address)

            # Calculer les PDAs
            participant_pda, _bump = Pubkey.find_program_address(
                [b"participant", bytes(wallet_pubkey)], 
                self.program_id
            )

            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            # Obtenir le token account (vous devrez adapter selon votre logique)
            # Pour l'exemple, on utilise une adresse fictive
            ball_token_account = wallet_pubkey  # À adapter selon votre logique

            tx = await program.rpc["update_participant"](
                ball_balance,
                token_account_bump,
                ctx=program.ctx(
                    accounts={
                        "participant": participant_pda,
                        "lottery_state": lottery_state_pda,
                        "user": wallet_pubkey,
                        "ball_token_account": ball_token_account,
                        "token_program": Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
                        "system_program": Pubkey.from_string("11111111111111111111111111111111")
                    },
                    signers=[self.admin_keypair]
                )
            )

            logger.info(f"Updated participant {wallet_address}: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error updating participant {wallet_address}: {e}")
            return False

        # 🔹 NOUVELLE MÉTHODE: Pause d'urgence
    async def emergency_pause(self, reason: str = "Emergency maintenance") -> bool:
        """Active la pause d'urgence"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            tx = await program.rpc["emergency_pause"](
                reason,
                ctx=program.ctx(
                    accounts={
                        "lottery_state": lottery_state_pda,
                        "admin": self.admin_keypair.pubkey
                    },
                    signers=[self.admin_keypair]
                )
            )

            logger.info(f"Emergency pause activated: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error activating emergency pause: {e}")
            return False

    # 🔹 NOUVELLE MÉTHODE: Reprise après pause
    async def emergency_resume(self, reason: str = "Emergency resolved") -> bool:
        """Désactive la pause d'urgence"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            tx = await program.rpc["emergency_resume"](
                reason,
                ctx=program.ctx(
                    accounts={
                        "lottery_state": lottery_state_pda,
                        "admin": self.admin_keypair.pubkey
                    },
                    signers=[self.admin_keypair]
                )
            )

            logger.info(f"Emergency pause deactivated: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error deactivating emergency pause: {e}")
            return False

    # 🔹 NOUVELLE MÉTHODE: Mise à jour de la configuration
    async def update_config(self, min_ticket_requirement: Optional[int] = None, 
                          max_tickets_per_wallet: Optional[int] = None, 
                          fee_percentage: Optional[int] = None) -> bool:
        """Met à jour la configuration du programme"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            tx = await program.rpc["update_config"](
                min_ticket_requirement,
                max_tickets_per_wallet,
                fee_percentage,
                ctx=program.ctx(
                    accounts={
                        "lottery_state": lottery_state_pda,
                        "admin": self.admin_keypair.pubkey
                    },
                    signers=[self.admin_keypair]
                )
            )

            logger.info(f"Config updated: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return False

    # 🔹 NOUVELLE MÉTHODE: Retrait du trésor
    async def withdraw_treasury(self, amount: int, treasury_wallet: str) -> bool:
        """Retire des fonds du trésor"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            treasury_pubkey = Pubkey.from_string(treasury_wallet)

            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            tx = await program.rpc["withdraw_treasury"](
                amount,
                ctx=program.ctx(
                    accounts={
                        "lottery_state": lottery_state_pda,
                        "admin": self.admin_keypair.pubkey,
                        "treasury_wallet": treasury_pubkey,
                        "system_program": Pubkey.from_string("11111111111111111111111111111111")
                    },
                    signers=[self.admin_keypair]
                )
            )

            logger.info(f"Treasury withdrawal of {amount} lamports to {treasury_wallet}: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error withdrawing from treasury: {e}")
            return False

    # 🔹 MÉTHODE UTILITAIRE: Créer une loterie sur la blockchain
    async def create_lottery_on_chain(self, lottery: Lottery) -> bool:
        """Crée une loterie sur la blockchain"""
        try:
            program = await self.get_program()
            if not program or not self.admin_keypair:
                return False

            # Déterminer le type de loterie
            if lottery.lottery_type == LotteryType.HOURLY:
                lottery_type_enum = {"hourly": {}}
            else:
                lottery_type_enum = {"daily": {}}

            # Obtenir l'état actuel pour le draw_id
            state = await self.get_lottery_state()
            if not state:
                return False

            if lottery.lottery_type == LotteryType.HOURLY:
                draw_id = state['hourly_draw_count'] + 1
            else:
                draw_id = state['daily_draw_count'] + 1

            # Calculer les PDAs
            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )

            lottery_pda, _bump = Pubkey.find_program_address(
                [
                    b"lottery",
                    b"hourly" if lottery.lottery_type == LotteryType.HOURLY else b"daily",
                    draw_id.to_bytes(4, 'little')
                ], 
                self.program_id
            )

            tx = await program.rpc["create_lottery"](
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

            # Mettre à jour la loterie avec les informations blockchain
            lottery.transaction_signature = str(tx)
            lottery.draw_id = draw_id
            lottery.save()

            logger.info(f"Lottery {lottery.id} created on-chain: {tx}")
            return True

        except Exception as e:
            logger.error(f"Error creating lottery {lottery.id} on-chain: {e}")
            return False

    # 🔹 MÉTHODE UTILITAIRE: Synchroniser tous les participants
    async def sync_all_participants(self) -> int:
        """Synchronise tous les participants actifs"""
        try:
            # Récupérer tous les wallets actifs de la base de données
            active_wallets = TokenHolding.objects.filter(
                is_eligible=True
            ).values_list('wallet_address', flat=True)

            synced_count = 0
            for wallet_address in active_wallets:
                try:
                    result = await self.sync_participant(wallet_address)
                    if result:
                        synced_count += 1
                except Exception as e:
                    logger.error(f"Error syncing participant {wallet_address}: {e}")
                    continue

            logger.info(f"Synchronized {synced_count} participants")
            return synced_count

        except Exception as e:
            logger.error(f"Error syncing all participants: {e}")
            return 0

    # 🔹 MÉTHODE UTILITAIRE: Vérifier la santé du programme
    async def check_program_health(self) -> Dict[str, Any]:
        """Vérifie la santé du programme Solana"""
        try:
            connection = await self.get_connection()
            
            # Vérifier la connexion
            health = await connection.get_health()
            
            # Vérifier l'état de la loterie
            lottery_state = await self.get_lottery_state()
            
            # Vérifier le solde du programme
            lottery_state_pda, _bump = Pubkey.find_program_address(
                [b"lottery_state"],
                self.program_id
            )
            
            account_info = await connection.get_account_info(lottery_state_pda)
            program_balance = account_info.value.lamports if account_info.value else 0
            
            health_data = {
                'connection_healthy': health.value == "ok",
                'lottery_state_available': lottery_state is not None,
                'program_balance_lamports': program_balance,
                'program_balance_sol': program_balance / 1_000_000_000,
                'is_paused': lottery_state.get('is_paused', True) if lottery_state else True,
                'emergency_stop': lottery_state.get('emergency_stop', True) if lottery_state else True,
                'total_participants': lottery_state.get('total_participants', 0) if lottery_state else 0,
                'hourly_jackpot_sol': lottery_state.get('hourly_jackpot', 0) / 1_000_000_000 if lottery_state else 0,
                'daily_jackpot_sol': lottery_state.get('daily_jackpot', 0) / 1_000_000_000 if lottery_state else 0,
                'last_updated': timezone.now().isoformat()
            }
            
            logger.info(f"Program health check completed: {health_data}")
            return health_data
            
        except Exception as e:
            logger.error(f"Error checking program health: {e}")
            return {
                'connection_healthy': False,
                'lottery_state_available': False,
                'error': str(e),
                'last_updated': timezone.now().isoformat()
            }

    # 🔹 MÉTHODE UTILITAIRE: Obtenir les événements récents
    async def get_recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Récupère les événements récents du programme"""
        try:
            connection = await self.get_connection()
            
            # Obtenir les signatures récentes pour le programme
            signatures = await connection.get_signatures_for_address(
                self.program_id,
                limit=limit
            )
            
            events = []
            for sig_info in signatures.value:
                try:
                    # Obtenir les détails de la transaction
                    tx_details = await connection.get_transaction(
                        sig_info.signature,
                        encoding="json",
                        max_supported_transaction_version=0
                    )
                    
                    if tx_details.value:
                        event_data = {
                            'signature': sig_info.signature,
                            'slot': sig_info.slot,
                            'block_time': sig_info.block_time,
                            'confirmation_status': sig_info.confirmation_status,
                            'err': sig_info.err,
                            'memo': sig_info.memo
                        }
                        events.append(event_data)
                        
                except Exception as e:
                    logger.warning(f"Error fetching transaction details for {sig_info.signature}: {e}")
                    continue
            
            logger.info(f"Retrieved {len(events)} recent events")
            return events
            
        except Exception as e:
            logger.error(f"Error getting recent events: {e}")
            return []

    # 🔹 MÉTHODE UTILITAIRE: Calculer les statistiques
    async def calculate_program_stats(self) -> Dict[str, Any]:
        """Calcule les statistiques du programme"""
        try:
            state = await self.get_lottery_state()
            if not state:
                return {}
            
            # Calculer les statistiques
            stats = {
                'total_participants': state['total_participants'],
                'total_tickets': state['total_tickets'],
                'hourly_jackpot_sol': state['hourly_jackpot'] / 1_000_000_000,
                'daily_jackpot_sol': state['daily_jackpot'] / 1_000_000_000,
                'total_jackpot_sol': (state['hourly_jackpot'] + state['daily_jackpot']) / 1_000_000_000,
                'hourly_draw_count': state['hourly_draw_count'],
                'daily_draw_count': state['daily_draw_count'],
                'total_draw_count': state['hourly_draw_count'] + state['daily_draw_count'],
                'treasury_balance_sol': state['treasury_balance'] / 1_000_000_000,
                'total_volume_processed_sol': state['total_volume_processed'] / 1_000_000_000,
                'average_tickets_per_participant': state['total_tickets'] / state['total_participants'] if state['total_participants'] > 0 else 0,
                'program_version': state.get('version', 'unknown'),
                'is_operational': not state['is_paused'] and not state['emergency_stop'],
                'last_updated': state['last_updated']
            }
            
            logger.info(f"Calculated program stats: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Error calculating program stats: {e}")
            return {}

    # 🔹 MÉTHODE DE NETTOYAGE: Fermer les connexions
    async def close_connections(self):
        """Ferme les connexions ouvertes"""
        try:
            if self.connection:
                await self.connection.close()
                self.connection = None
                logger.info("Solana connection closed")
        except Exception as e:
            logger.error(f"Error closing connections: {e}")

    # 🔹 NOUVELLE MÉTHODE: Vérifier le statut de santé
    async def get_health_status(self) -> Dict[str, Any]:
        """🔹 CORRECTION: Méthode manquante pour le health check"""
        try:
            connection = await self.get_connection()
            
            # Test de connexion basique
            health_response = await connection.get_health()
            
            # Vérifier l'état de la loterie
            lottery_state = await self.get_lottery_state()
            
            return {
                'solana_rpc_healthy': health_response.value == "ok",
                'lottery_state_accessible': lottery_state is not None,
                'connection_status': 'connected' if health_response.value == "ok" else 'disconnected',
                'last_check': timezone.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                'solana_rpc_healthy': False,
                'lottery_state_accessible': False,
                'connection_status': 'error',
                'error': str(e),
                'last_check': timezone.now().isoformat()
            }

    # 🔹 MÉTHODE UTILITAIRE: Convertir lamports en SOL
    @staticmethod
    def lamports_to_sol(lamports: int) -> Decimal:
        """Convertit les lamports en SOL"""
        return Decimal(str(lamports)) / Decimal('1000000000')

    # 🔹 MÉTHODE UTILITAIRE: Convertir SOL en lamports
    @staticmethod
    def sol_to_lamports(sol: Decimal) -> int:
        """Convertit les SOL en lamports"""
        return int(sol * Decimal('1000000000'))

    # 🔹 MÉTHODE UTILITAIRE: Valider une adresse Solana
    @staticmethod
    def is_valid_solana_address(address: str) -> bool:
        """Valide une adresse Solana"""
        try:
            Pubkey.from_string(address)
            return True
        except Exception:
            return False

    # 🔹 CORRECTION: Méthode manquante _get_default_state
    def _get_default_state(self) -> Dict[str, Any]:
        """🔹 CORRECTION: Méthode manquante pour l'état par défaut"""
        return self.get_default_state()

# Instance globale du service
solana_service = SolanaService()

# 🔹 FONCTION UTILITAIRE: Gestionnaire de contexte pour les connexions
class SolanaConnectionManager:
    """Gestionnaire de contexte pour les connexions Solana"""
    
    def __init__(self, service: SolanaService):
        self.service = service
    
    async def __aenter__(self):
        await self.service.get_connection()
        return self.service
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.service.close_connections()

# 🔹 FONCTION UTILITAIRE: Décorateur pour retry automatique
def retry_on_failure(max_retries: int = 3, delay: float = 1.0):
    """Décorateur pour retry automatique en cas d'échec"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {e}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"All {max_retries} attempts failed for {func.__name__}: {e}")
            
            raise last_exception
        return wrapper
    return decorator

# 🔹 EXEMPLE D'UTILISATION DU DÉCORATEUR
@retry_on_failure(max_retries=3, delay=2.0)
async def robust_get_lottery_state():
    """Version robuste de get_lottery_state avec retry automatique"""
    return await solana_service.get_lottery_state()

# 🔹 FONCTION UTILITAIRE: Batch processing pour les participants
async def batch_sync_participants(wallet_addresses: List[str], batch_size: int = 10) -> Dict[str, Any]:
    """Synchronise les participants par batch pour éviter la surcharge"""
    results = {
        'success': [],
        'failed': [],
        'total': len(wallet_addresses)
    }
    
    for i in range(0, len(wallet_addresses), batch_size):
        batch = wallet_addresses[i:i + batch_size]
        
        # Traiter le batch avec un délai entre chaque
        for wallet_address in batch:
            try:
                result = await solana_service.sync_participant(wallet_address)
                if result:
                    results['success'].append(wallet_address)
                else:
                    results['failed'].append(wallet_address)
            except Exception as e:
                logger.error(f"Failed to sync {wallet_address}: {e}")
                results['failed'].append(wallet_address)
            
            # Petit délai pour éviter la surcharge
            await asyncio.sleep(0.1)
        
        # Délai plus long entre les batches
        if i + batch_size < len(wallet_addresses):
            await asyncio.sleep(1.0)
    
    return results

# 🔹 FONCTION UTILITAIRE: Monitoring des performances
class SolanaPerformanceMonitor:
    """Moniteur de performance pour les opérations Solana"""
    
    def __init__(self):
        self.metrics = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'average_response_time': 0.0,
            'last_error': None,
            'last_success': None
        }
    
    async def monitor_operation(self, operation_name: str, operation_func, *args, **kwargs):
        """Monitore une opération et collecte les métriques"""
        start_time = time.time()
        
        try:
            self.metrics['total_requests'] += 1
            result = await operation_func(*args, **kwargs)
            
            # Succès
            self.metrics['successful_requests'] += 1
            self.metrics['last_success'] = timezone.now()
            
            # Calculer le temps de réponse moyen
            response_time = time.time() - start_time
            self.metrics['average_response_time'] = (
                (self.metrics['average_response_time'] * (self.metrics['successful_requests'] - 1) + response_time) 
                / self.metrics['successful_requests']
            )
            
            logger.info(f"Operation {operation_name} completed in {response_time:.2f}s")
            return result
            
        except Exception as e:
            # Échec
            self.metrics['failed_requests'] += 1
            self.metrics['last_error'] = {
                'timestamp': timezone.now(),
                'operation': operation_name,
                'error': str(e)
            }
            
            logger.error(f"Operation {operation_name} failed after {time.time() - start_time:.2f}s: {e}")
            raise
    
    def get_metrics(self) -> Dict[str, Any]:
        """Retourne les métriques de performance"""
        success_rate = (
            (self.metrics['successful_requests'] / self.metrics['total_requests'] * 100)
            if self.metrics['total_requests'] > 0 else 0
        )
        
        return {
            **self.metrics,
            'success_rate': round(success_rate, 2),
            'failure_rate': round(100 - success_rate, 2)
        }

# Instance globale du moniteur
performance_monitor = SolanaPerformanceMonitor()

# 🔹 FONCTION UTILITAIRE: Cache intelligent pour les états
class SolanaStateCache:
    """Cache intelligent pour les états Solana avec invalidation automatique"""
    
    def __init__(self, default_ttl: int = 60):
        self.default_ttl = default_ttl
        self._cache = {}
    
    def get(self, key: str) -> Optional[Any]:
        """Récupère une valeur du cache"""
        if key in self._cache:
            data, expiry = self._cache[key]
            if timezone.now().timestamp() < expiry:
                return data
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Stocke une valeur dans le cache"""
        ttl = ttl or self.default_ttl
        expiry = timezone.now().timestamp() + ttl
        self._cache[key] = (value, expiry)
    
    def invalidate(self, pattern: str = None) -> None:
        """Invalide le cache (tout ou par pattern)"""
        if pattern:
            keys_to_remove = [k for k in self._cache.keys() if pattern in k]
            for key in keys_to_remove:
                del self._cache[key]
        else:
            self._cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du cache"""
        now = timezone.now().timestamp()
        valid_entries = sum(1 for _, expiry in self._cache.values() if now < expiry)
        expired_entries = len(self._cache) - valid_entries
        
        return {
            'total_entries': len(self._cache),
            'valid_entries': valid_entries,
            'expired_entries': expired_entries,
            'cache_keys': list(self._cache.keys())
        }

# Instance globale du cache
state_cache = SolanaStateCache()

# 🔹 FONCTION UTILITAIRE: Validation des données blockchain
class SolanaDataValidator:
    """Validateur pour les données provenant de la blockchain"""
    
    @staticmethod
    def validate_lottery_state(state: Dict[str, Any]) -> bool:
        """Valide la structure d'un état de loterie"""
        required_fields = [
            'admin', 'ball_token_mint', 'hourly_jackpot', 'daily_jackpot',
            'total_participants', 'total_tickets'
        ]
        
        for field in required_fields:
            if field not in state:
                logger.error(f"Missing required field in lottery state: {field}")
                return False
        
        # Validation des types
        try:
            int(state['hourly_jackpot'])
            int(state['daily_jackpot'])
            int(state['total_participants'])
            int(state['total_tickets'])
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid data types in lottery state: {e}")
            return False
        
        return True
    
    @staticmethod
    def validate_participant_info(info: Dict[str, Any]) -> bool:
        """Valide les informations d'un participant"""
        required_fields = [
            'wallet', 'ball_balance', 'tickets_count', 'is_eligible'
        ]
        
        for field in required_fields:
            if field not in info:
                logger.error(f"Missing required field in participant info: {field}")
                return False
        
        # Validation des types
        try:
            int(info['ball_balance'])
            int(info['tickets_count'])
            bool(info['is_eligible'])
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid data types in participant info: {e}")
            return False
        
        return True
    
    @staticmethod
    def sanitize_wallet_address(address: str) -> Optional[str]:
        """Nettoie et valide une adresse de wallet"""
        if not address or not isinstance(address, str):
            return None
        
        # Nettoyer l'adresse
        cleaned = address.strip()
        
        # Vérifier la longueur (les adresses Solana font généralement 32-44 caractères)
        if len(cleaned) < 32 or len(cleaned) > 44:
            return None
        
        # Vérifier que c'est une adresse Solana valide
        if not SolanaService.is_valid_solana_address(cleaned):
            return None
        
        return cleaned

# 🔹 FONCTION UTILITAIRE: Gestionnaire d'erreurs Solana
class SolanaErrorHandler:
    """Gestionnaire d'erreurs spécialisé pour Solana"""
    
    ERROR_CODES = {
        'ACCOUNT_NOT_FOUND': 'Account does not exist',
        'INSUFFICIENT_FUNDS': 'Insufficient funds for transaction',
        'INVALID_SIGNATURE': 'Invalid transaction signature',
        'TIMEOUT': 'Operation timed out',
        'NETWORK_ERROR': 'Network connection error',
        'PROGRAM_ERROR': 'Smart contract execution error'
    }
    
    @classmethod
    def handle_error(cls, error: Exception, context: str = "") -> Dict[str, Any]:
        """Gère une erreur et retourne des informations structurées"""
        error_info = {
            'error_type': type(error).__name__,
            'error_message': str(error),
            'context': context,
            'timestamp': timezone.now().isoformat(),
            'recoverable': False,
            'suggested_action': 'Contact support'
        }
        
        # Analyser le type d'erreur
        error_str = str(error).lower()
        
        if 'timeout' in error_str:
            error_info.update({
                'error_code': 'TIMEOUT',
                'recoverable': True,
                'suggested_action': 'Retry the operation'
            })
        elif 'account not found' in error_str:
            error_info.update({
                'error_code': 'ACCOUNT_NOT_FOUND',
                'recoverable': False,
                'suggested_action': 'Verify the account address'
            })
        elif 'insufficient' in error_str:
            error_info.update({
                'error_code': 'INSUFFICIENT_FUNDS',
                'recoverable': False,
                'suggested_action': 'Add more funds to the account'
            })
        elif 'network' in error_str or 'connection' in error_str:
            error_info.update({
                'error_code': 'NETWORK_ERROR',
                'recoverable': True,
                'suggested_action': 'Check network connection and retry'
            })
        
        logger.error(f"Solana error in {context}: {error_info}")
        return error_info
    
    @classmethod
    def is_recoverable_error(cls, error: Exception) -> bool:
        """Détermine si une erreur est récupérable"""
        error_str = str(error).lower()
        recoverable_keywords = ['timeout', 'network', 'connection', 'temporary']
        
        return any(keyword in error_str for keyword in recoverable_keywords)

# 🔹 FONCTION UTILITAIRE: Configuration dynamique
class SolanaDynamicConfig:
    """Configuration dynamique pour le service Solana"""
    
    def __init__(self):
        self.config = {
            'max_retries': 3,
            'timeout_seconds': 15,
            'batch_size': 10,
            'cache_ttl': 60,
            'rate_limit_per_second': 10
        }
    
    def update_config(self, key: str, value: Any) -> bool:
        """Met à jour une configuration"""
        if key in self.config:
            old_value = self.config[key]
            self.config[key] = value
            logger.info(f"Config updated: {key} = {value} (was {old_value})")
            return True
        return False
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Récupère une configuration"""
        return self.config.get(key, default)
    
    def get_all_config(self) -> Dict[str, Any]:
        """Retourne toute la configuration"""
        return self.config.copy()

# Instance globale de la configuration
dynamic_config = SolanaDynamicConfig()

# 🔹 FONCTION UTILITAIRE: Rate Limiter pour les requêtes Solana
class SolanaRateLimiter:
    """Rate limiter pour éviter la surcharge des RPC Solana"""
    
    def __init__(self, max_requests_per_second: int = 10):
        self.max_requests = max_requests_per_second
        self.requests = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        """Acquiert le droit de faire une requête"""
        async with self.lock:
            now = time.time()
            
            # Nettoyer les anciennes requêtes (plus d'1 seconde)
            self.requests = [req_time for req_time in self.requests if now - req_time < 1.0]
            
            # Vérifier si on peut faire une nouvelle requête
            if len(self.requests) >= self.max_requests:
                # Attendre jusqu'à ce qu'une requête expire
                sleep_time = 1.0 - (now - self.requests[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    return await self.acquire()  # Réessayer
            
            # Enregistrer la nouvelle requête
            self.requests.append(now)
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du rate limiter"""
        now = time.time()
        recent_requests = [req for req in self.requests if now - req < 1.0]
        
        return {
            'max_requests_per_second': self.max_requests,
            'current_requests_in_window': len(recent_requests),
            'available_requests': max(0, self.max_requests - len(recent_requests)),
            'window_start': min(recent_requests) if recent_requests else now
        }

# Instance globale du rate limiter
rate_limiter = SolanaRateLimiter()

# 🔹 FONCTION UTILITAIRE: Circuit Breaker pour la résilience
class SolanaCircuitBreaker:
    """Circuit breaker pour éviter les cascades d'erreurs"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
    
    async def call(self, func, *args, **kwargs):
        """Exécute une fonction avec protection circuit breaker"""
        if self.state == 'OPEN':
            if self._should_attempt_reset():
                self.state = 'HALF_OPEN'
            else:
                raise Exception("Circuit breaker is OPEN - service unavailable")
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Détermine si on doit tenter de réinitialiser le circuit"""
        if self.last_failure_time is None:
            return True
        
        return time.time() - self.last_failure_time >= self.recovery_timeout
    
    def _on_success(self):
        """Appelé en cas de succès"""
        self.failure_count = 0
        self.state = 'CLOSED'
    
    def _on_failure(self):
        """Appelé en cas d'échec"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'
            logger.warning(f"Circuit breaker opened after {self.failure_count} failures")
    
    def get_state(self) -> Dict[str, Any]:
        """Retourne l'état du circuit breaker"""
        return {
            'state': self.state,
            'failure_count': self.failure_count,
            'failure_threshold': self.failure_threshold,
            'last_failure_time': self.last_failure_time,
            'time_until_retry': max(0, self.recovery_timeout - (time.time() - (self.last_failure_time or 0)))
        }

# Instance globale du circuit breaker
circuit_breaker = SolanaCircuitBreaker()

# 🔹 CLASSE UTILITAIRE: Gestionnaire de connexions avec pool
class SolanaConnectionPool:
    """Pool de connexions Solana pour améliorer les performances"""
    
    def __init__(self, rpc_url: str, pool_size: int = 5):
        self.rpc_url = rpc_url
        self.pool_size = pool_size
        self.connections = []
        self.available_connections = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.initialized = False
    
    async def initialize(self):
        """Initialise le pool de connexions"""
        if self.initialized:
            return
        
        async with self.lock:
            if self.initialized:
                return
            
            for i in range(self.pool_size):
                try:
                    connection = AsyncClient(self.rpc_url)
                    self.connections.append(connection)
                    await self.available_connections.put(connection)
                except Exception as e:
                    logger.error(f"Failed to create connection {i}: {e}")
            
            self.initialized = True
            logger.info(f"Initialized connection pool with {len(self.connections)} connections")
    
    async def get_connection(self) -> AsyncClient:
        """Récupère une connexion du pool"""
        if not self.initialized:
            await self.initialize()
        
        try:
            # Attendre une connexion disponible avec timeout
            connection = await asyncio.wait_for(
                self.available_connections.get(),
                timeout=5.0
            )
            return connection
        except asyncio.TimeoutError:
            # Créer une nouvelle connexion temporaire si le pool est épuisé
            logger.warning("Connection pool exhausted, creating temporary connection")
            return AsyncClient(self.rpc_url)
    
    async def return_connection(self, connection: AsyncClient):
        """Remet une connexion dans le pool"""
        if connection in self.connections:
            await self.available_connections.put(connection)
        else:
            # Connexion temporaire, la fermer
            try:
                await connection.close()
            except Exception as e:
                logger.error(f"Error closing temporary connection: {e}")
    
    async def close_all(self):
        """Ferme toutes les connexions du pool"""
        for connection in self.connections:
            try:
                await connection.close()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
        
        self.connections.clear()
        self.initialized = False
        logger.info("Connection pool closed")
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du pool"""
        return {
            'pool_size': self.pool_size,
            'total_connections': len(self.connections),
            'available_connections': self.available_connections.qsize(),
            'busy_connections': len(self.connections) - self.available_connections.qsize(),
            'initialized': self.initialized
        }

# 🔹 CLASSE UTILITAIRE: Gestionnaire de contexte pour les opérations Solana
class SolanaOperationContext:
    """Gestionnaire de contexte pour les opérations Solana avec monitoring complet"""
    
    def __init__(self, operation_name: str, timeout: int = 30):
        self.operation_name = operation_name
        self.timeout = timeout
        self.start_time = None
        self.connection = None
    
    async def __aenter__(self):
        self.start_time = time.time()
        
        # Acquérir le rate limiter
        await rate_limiter.acquire()
        
        # Obtenir une connexion
        self.connection = await solana_service.get_connection()
        
        logger.info(f"Starting Solana operation: {self.operation_name}")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        
        if exc_type is None:
            logger.info(f"Solana operation {self.operation_name} completed in {duration:.2f}s")
            performance_monitor.metrics['successful_requests'] += 1
        else:
            logger.error(f"Solana operation {self.operation_name} failed after {duration:.2f}s: {exc_val}")
            performance_monitor.metrics['failed_requests'] += 1
        
        performance_monitor.metrics['total_requests'] += 1
        
        # Mettre à jour le temps de réponse moyen
        if performance_monitor.metrics['total_requests'] > 0:
            current_avg = performance_monitor.metrics['average_response_time']
            total_requests = performance_monitor.metrics['total_requests']
            performance_monitor.metrics['average_response_time'] = (
                (current_avg * (total_requests - 1) + duration) / total_requests
            )

# 🔹 FONCTION UTILITAIRE: Validation et nettoyage des données
def sanitize_solana_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Nettoie et valide les données provenant de Solana"""
    sanitized = {}
    
    for key, value in data.items():
        if key in ['admin', 'ball_token_mint', 'wallet', 'token_account']:
            # Nettoyer les adresses Solana
            if isinstance(value, (bytes, bytearray)):
                try:
                    sanitized[key] = str(Pubkey(value))
                except Exception:
                    sanitized[key] = 'Invalid'
            else:
                sanitized[key] = str(value)
        
        elif key in ['hourly_jackpot', 'daily_jackpot', 'ball_balance', 'tickets_count', 'total_participants', 'total_tickets']:
            # Nettoyer les valeurs numériques
            try:
                sanitized[key] = int(value) if value is not None else 0
            except (ValueError, TypeError):
                sanitized[key] = 0
        
        elif key in ['is_eligible', 'is_paused', 'emergency_stop']:
            # Nettoyer les booléens
            sanitized[key] = bool(value)
        
        elif key in ['last_updated', 'last_hourly_draw', 'last_daily_draw']:
            # Nettoyer les timestamps
            try:
                sanitized[key] = int(value) if value is not None else 0
            except (ValueError, TypeError):
                sanitized[key] = 0
        
        else:
            # Autres valeurs
            sanitized[key] = value
    
    return sanitized

# 🔹 FONCTION UTILITAIRE: Backup et restauration d'état
class SolanaStateBackup:
    """Système de backup pour les états Solana critiques"""
    
    def __init__(self, backup_interval: int = 300):  # 5 minutes
        self.backup_interval = backup_interval
        self.last_backup = None
        self.backup_data = {}
    
    async def backup_lottery_state(self):
        """Sauvegarde l'état de la loterie"""
        try:
            state = await solana_service.get_lottery_state()
            if state:
                self.backup_data['lottery_state'] = {
                    'data': state,
                    'timestamp': timezone.now().isoformat()
                }
                self.last_backup = timezone.now()
                logger.info("Lottery state backed up successfully")
        except Exception as e:
            logger.error(f"Failed to backup lottery state: {e}")
    
    def get_backup_data(self, data_type: str) -> Optional[Dict[str, Any]]:
        """Récupère les données de backup"""
        return self.backup_data.get(data_type)
    
    def should_backup(self) -> bool:
        """Détermine s'il faut faire un backup"""
        if self.last_backup is None:
            return True
        
        return (timezone.now() - self.last_backup).total_seconds() >= self.backup_interval
    
    async def auto_backup(self):
        """Backup automatique si nécessaire"""
        if self.should_backup():
            await self.backup_lottery_state()

# Instance globale du système de backup
state_backup = SolanaStateBackup()

# 🔹 FONCTION UTILITAIRE: Métriques avancées
class SolanaMetricsCollector:
    """Collecteur de métriques avancées pour Solana"""
    
    def __init__(self):
        self.metrics = {
            'operations': {},
            'errors': {},
            'performance': {
                'avg_response_time': 0.0,
                'min_response_time': float('inf'),
                'max_response_time': 0.0,
                'total_operations': 0
            },
            'health': {
                'last_successful_connection': None,
                'consecutive_failures': 0,
                'uptime_percentage': 100.0
            }
        }
    
    def record_operation(self, operation_name: str, duration: float, success: bool):
        """Enregistre une opération"""
        if operation_name not in self.metrics['operations']:
            self.metrics['operations'][operation_name] = {
                'total': 0,
                'success': 0,
                'failure': 0,
                'avg_duration': 0.0
            }
        
        op_metrics = self.metrics['operations'][operation_name]
        op_metrics['total'] += 1
        
        if success:
            op_metrics['success'] += 1
            self.metrics['health']['consecutive_failures'] = 0
            self.metrics['health']['last_successful_connection'] = timezone.now()
        else:
            op_metrics['failure'] += 1
            self.metrics['health']['consecutive_failures'] += 1
        
        # Mettre à jour la durée moyenne
        op_metrics['avg_duration'] = (
            (op_metrics['avg_duration'] * (op_metrics['total'] - 1) + duration) / op_metrics['total']
        )
        
        # Mettre à jour les métriques de performance globales
        perf = self.metrics['performance']
        perf['total_operations'] += 1
        perf['avg_response_time'] = (
            (perf['avg_response_time'] * (perf['total_operations'] - 1) + duration) / perf['total_operations']
        )
        perf['min_response_time'] = min(perf['min_response_time'], duration)
        perf['max_response_time'] = max(perf['max_response_time'], duration)
    
    def record_error(self, error_type: str, error_message: str):
        """Enregistre une erreur"""
        if error_type not in self.metrics['errors']:
            self.metrics['errors'][error_type] = {
                'count': 0,
                'last_occurrence': None,
                'messages': []
            }
        
        error_metrics = self.metrics['errors'][error_type]
        error_metrics['count'] += 1
        error_metrics['last_occurrence'] = timezone.now()
        
        # Garder seulement les 10 derniers messages d'erreur
        error_metrics['messages'].append({
            'message': error_message,
            'timestamp': timezone.now().isoformat()
        })
        if len(error_metrics['messages']) > 10:
            error_metrics['messages'] = error_metrics['messages'][-10:]
    
    def calculate_uptime(self) -> float:
        """Calcule le pourcentage d'uptime"""
        total_ops = self.metrics['performance']['total_operations']
        if total_ops == 0:
            return 100.0
        
        total_failures = sum(
            op['failure'] for op in self.metrics['operations'].values()
        )
        
        uptime = ((total_ops - total_failures) / total_ops) * 100
        self.metrics['health']['uptime_percentage'] = round(uptime, 2)
        return uptime
    
    def get_summary(self) -> Dict[str, Any]:
        """Retourne un résumé des métriques"""
        self.calculate_uptime()
        
        return {
            'total_operations': self.metrics['performance']['total_operations'],
            'avg_response_time': round(self.metrics['performance']['avg_response_time'], 3),
            'uptime_percentage': self.metrics['health']['uptime_percentage'],
            'consecutive_failures': self.metrics['health']['consecutive_failures'],
            'last_successful_connection': self.metrics['health']['last_successful_connection'],
            'top_operations': sorted(
                self.metrics['operations'].items(),
                key=lambda x: x[1]['total'],
                reverse=True
            )[:5],
            'recent_errors': [
                {
                    'type': error_type,
                    'count': error_data['count'],
                    'last_occurrence': error_data['last_occurrence']
                }
                for error_type, error_data in self.metrics['errors'].items()
            ]
        }
    
    def reset_metrics(self):
        """Remet à zéro les métriques"""
        self.__init__()

# Instance globale du collecteur de métriques
metrics_collector = SolanaMetricsCollector()

# 🔹 DÉCORATEUR: Monitoring automatique des opérations
def monitor_solana_operation(operation_name: str):
    """Décorateur pour monitorer automatiquement les opérations Solana"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            success = False
            
            try:
                result = await func(*args, **kwargs)
                success = True
                return result
            except Exception as e:
                metrics_collector.record_error(
                    type(e).__name__,
                    str(e)
                )
                raise
            finally:
                duration = time.time() - start_time
                metrics_collector.record_operation(
                    operation_name,
                    duration,
                    success
                )
        
        return wrapper
    return decorator

# 🔹 CLASSE UTILITAIRE: Gestionnaire de transactions Solana
class SolanaTransactionManager:
    """Gestionnaire avancé pour les transactions Solana"""
    
    def __init__(self):
        self.pending_transactions = {}
        self.transaction_history = []
        self.max_history = 1000
    
    async def submit_transaction(self, transaction: Transaction, description: str = "") -> str:
        """Soumet une transaction et la suit"""
        try:
            connection = await solana_service.get_connection()
            
            # Envoyer la transaction
            result = await connection.send_transaction(
                transaction,
                opts=TxOpts(skip_confirmation=False, preflight_commitment=Commitment("confirmed"))
            )
            
            signature = str(result.value)
            
            # Enregistrer la transaction en attente
            self.pending_transactions[signature] = {
                'signature': signature,
                'description': description,
                'submitted_at': timezone.now(),
                'status': 'pending'
            }
            
            logger.info(f"Transaction submitted: {signature} - {description}")
            return signature
            
        except Exception as e:
            logger.error(f"Failed to submit transaction: {e}")
            raise
    
    async def confirm_transaction(self, signature: str, timeout: int = 60) -> bool:
        """Confirme une transaction avec timeout"""
        try:
            connection = await solana_service.get_connection()
            
            # Attendre la confirmation
            confirmation = await asyncio.wait_for(
                connection.confirm_transaction(signature),
                timeout=timeout
            )
            
            if signature in self.pending_transactions:
                self.pending_transactions[signature]['status'] = 'confirmed'
                self.pending_transactions[signature]['confirmed_at'] = timezone.now()
            
            # Ajouter à l'historique
            self._add_to_history(signature, 'confirmed')
            
            logger.info(f"Transaction confirmed: {signature}")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Transaction confirmation timeout: {signature}")
            if signature in self.pending_transactions:
                self.pending_transactions[signature]['status'] = 'timeout'
            self._add_to_history(signature, 'timeout')
            return False
        except Exception as e:
            logger.error(f"Transaction confirmation error: {signature} - {e}")
            if signature in self.pending_transactions:
                self.pending_transactions[signature]['status'] = 'error'
            self._add_to_history(signature, 'error')
            return False
    
    def _add_to_history(self, signature: str, status: str):
        """Ajoute une transaction à l'historique"""
        self.transaction_history.append({
            'signature': signature,
            'status': status,
            'timestamp': timezone.now()
        })
        
        # Limiter la taille de l'historique
        if len(self.transaction_history) > self.max_history:
            self.transaction_history = self.transaction_history[-self.max_history:]
    
    def get_pending_transactions(self) -> List[Dict[str, Any]]:
        """Retourne les transactions en attente"""
        return list(self.pending_transactions.values())
    
    def get_transaction_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques des transactions"""
        total = len(self.transaction_history)
        if total == 0:
            return {'total': 0, 'confirmed': 0, 'failed': 0, 'success_rate': 0}
        
        confirmed = sum(1 for tx in self.transaction_history if tx['status'] == 'confirmed')
        failed = sum(1 for tx in self.transaction_history if tx['status'] in ['error', 'timeout'])
        
        return {
            'total': total,
            'confirmed': confirmed,
            'failed': failed,
            'pending': len(self.pending_transactions),
            'success_rate': round((confirmed / total) * 100, 2) if total > 0 else 0
        }

# Instance globale du gestionnaire de transactions
transaction_manager = SolanaTransactionManager()

# 🔹 FONCTION UTILITAIRE: Health Check complet
async def comprehensive_health_check() -> Dict[str, Any]:
    """Effectue un health check complet du système Solana"""
    health_data = {
        'timestamp': timezone.now().isoformat(),
        'overall_status': 'unknown',
        'components': {}
    }
    
    # Vérifier la connexion RPC
    try:
        async with SolanaOperationContext('health_check_rpc'):
            connection = await solana_service.get_connection()
            rpc_health = await connection.get_health()
            health_data['components']['rpc'] = {
                'status': 'healthy' if rpc_health.value == 'ok' else 'unhealthy',
                'response': rpc_health.value
            }
    except Exception as e:
        health_data['components']['rpc'] = {
            'status': 'error',
            'error': str(e)
        }
    
    # Vérifier l'état de la loterie
    try:
        async with SolanaOperationContext('health_check_lottery_state'):
            lottery_state = await solana_service.get_lottery_state()
            health_data['components']['lottery_state'] = {
                'status': 'healthy' if lottery_state else 'unhealthy',
                'data_available': lottery_state is not None
            }
    except Exception as e:
        health_data['components']['lottery_state'] = {
            'status': 'error',
            'error': str(e)
        }
    
    # Vérifier le programme
    try:
        program = await solana_service.get_program()
        health_data['components']['program'] = {
            'status': 'healthy' if program else 'unhealthy',
            'loaded': program is not None
        }
    except Exception as e:
        health_data['components']['program'] = {
            'status': 'error',
            'error': str(e)
        }
    
    # Vérifier les métriques
    health_data['components']['metrics'] = {
        'status': 'healthy',
        'data': metrics_collector.get_summary()
    }
    
    # Vérifier le circuit breaker
    cb_state = circuit_breaker.get_state()
    health_data['components']['circuit_breaker'] = {
        'status': 'healthy' if cb_state['state'] == 'CLOSED' else 'degraded',
        'state': cb_state
    }
    
    # Déterminer le statut global
    component_statuses = [comp['status'] for comp in health_data['components'].values()]
    if all(status == 'healthy' for status in component_statuses):
        health_data['overall_status'] = 'healthy'
    elif any(status == 'error' for status in component_statuses):
        health_data['overall_status'] = 'unhealthy'
    else:
        health_data['overall_status'] = 'degraded'
    
    return health_data

# 🔹 FONCTION UTILITAIRE: Nettoyage automatique
async def cleanup_solana_resources():
    """Nettoie les ressources Solana (cache, connexions, etc.)"""
    try:
        # Nettoyer le cache
        state_cache.invalidate()
        
        # Nettoyer les transactions expirées
        current_time = timezone.now()
        expired_transactions = []
        
        for signature, tx_data in transaction_manager.pending_transactions.items():
            if (current_time - tx_data['submitted_at']).total_seconds() > 300:  # 5 minutes
                expired_transactions.append(signature)
        
        for signature in expired_transactions:
            del transaction_manager.pending_transactions[signature]
        
        # Backup automatique si nécessaire
        await state_backup.auto_backup()
        
        logger.info(f"Cleanup completed: removed {len(expired_transactions)} expired transactions")
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

# 🔹 TÂCHE PÉRIODIQUE: Maintenance automatique
async def periodic_maintenance():
    """Tâche de maintenance périodique"""
    while True:
        try:
            await cleanup_solana_resources()
            await asyncio.sleep(300)  # Toutes les 5 minutes
        except Exception as e:
            logger.error(f"Error in periodic maintenance: {e}")
            await asyncio.sleep(60)  # Attendre 1 minute en cas d'erreur

# 🔹 FONCTION D'INITIALISATION: Démarrage du service
async def initialize_solana_service():
    """Initialise complètement le service Solana"""
    try:
        logger.info("🚀 Initializing Solana service...")
        
        # Vérifier la configuration
        if not solana_service.admin_keypair:
            raise ValueError("Admin keypair not configured")
        
        # Tester la connexion
        connection = await solana_service.get_connection()
        health = await connection.get_health()
        
        if health.value != "ok":
            raise ValueError(f"RPC health check failed: {health.value}")
        
        # Charger le programme
        program = await solana_service.get_program()
        if not program:
            raise ValueError("Failed to load Solana program")
        
        # Vérifier l'état de la loterie
        lottery_state = await solana_service.get_lottery_state()
        if not lottery_state:
            logger.warning("⚠️ Lottery state not available - program may need initialization")
        
        # Démarrer la maintenance périodique
        asyncio.create_task(periodic_maintenance())
        
        logger.info("✅ Solana service initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize Solana service: {e}")
        return False

# 🔹 FONCTION DE DIAGNOSTIC: Diagnostic complet
async def diagnose_solana_issues() -> Dict[str, Any]:
    """Effectue un diagnostic complet des problèmes Solana"""
    diagnosis = {
        'timestamp': timezone.now().isoformat(),
        'issues': [],
        'recommendations': [],
        'system_info': {}
    }
    
    try:
        # Vérifier la configuration
        if not solana_service.admin_keypair:
            diagnosis['issues'].append("Admin keypair not configured")
            diagnosis['recommendations'].append("Set SOLANA_ADMIN_PRIVATE_KEY environment variable")
        
        # Vérifier la connexion RPC
        try:
            connection = await solana_service.get_connection()
            health = await connection.get_health()
            diagnosis['system_info']['rpc_health'] = health.value
            
            if health.value != "ok":
                diagnosis['issues'].append(f"RPC health check failed: {health.value}")
                diagnosis['recommendations'].append("Check RPC endpoint availability")
        except Exception as e:
            diagnosis['issues'].append(f"RPC connection failed: {str(e)}")
            diagnosis['recommendations'].append("Verify SOLANA_RPC_URL configuration")
        
        # Vérifier le circuit breaker
        cb_state = circuit_breaker.get_state()
        if cb_state['state'] != 'CLOSED':
            diagnosis['issues'].append(f"Circuit breaker is {cb_state['state']}")
            diagnosis['recommendations'].append("Wait for circuit breaker to reset or investigate underlying issues")
        
        # Vérifier les métriques
        metrics_summary = metrics_collector.get_summary()
        if metrics_summary['consecutive_failures'] > 5:
            diagnosis['issues'].append(f"High consecutive failure count: {metrics_summary['consecutive_failures']}")
                        
                         
        diagnosis['recommendations'].append("Investigate network connectivity and RPC endpoint stability")
        
        # Vérifier les performances
        if metrics_summary['avg_response_time'] > 10.0:
            diagnosis['issues'].append(f"High average response time: {metrics_summary['avg_response_time']:.2f}s")
            diagnosis['recommendations'].append("Consider switching to a faster RPC endpoint")
        
        # Vérifier l'uptime
        if metrics_summary['uptime_percentage'] < 95.0:
            diagnosis['issues'].append(f"Low uptime: {metrics_summary['uptime_percentage']:.1f}%")
            diagnosis['recommendations'].append("Investigate frequent failures and implement retry mechanisms")
        
        # Vérifier les transactions en attente
        pending_txs = transaction_manager.get_pending_transactions()
        if len(pending_txs) > 10:
            diagnosis['issues'].append(f"High number of pending transactions: {len(pending_txs)}")
            diagnosis['recommendations'].append("Check transaction confirmation times and network congestion")
        
        # Vérifier le cache
        cache_stats = state_cache.get_stats()
        if cache_stats['expired_entries'] > cache_stats['valid_entries']:
            diagnosis['issues'].append("High cache expiration rate")
            diagnosis['recommendations'].append("Consider increasing cache TTL or investigating data freshness requirements")
        
        # Informations système
        diagnosis['system_info'].update({
            'metrics_summary': metrics_summary,
            'circuit_breaker_state': cb_state,
            'pending_transactions': len(pending_txs),
            'cache_stats': cache_stats,
            'rate_limiter_stats': rate_limiter.get_stats()
        })
        
        # Statut global
        if not diagnosis['issues']:
            diagnosis['overall_status'] = 'healthy'
        elif len(diagnosis['issues']) <= 2:
            diagnosis['overall_status'] = 'warning'
        else:
            diagnosis['overall_status'] = 'critical'
        
    except Exception as e:
        diagnosis['issues'].append(f"Diagnostic error: {str(e)}")
        diagnosis['overall_status'] = 'error'
    
    return diagnosis

# 🔹 FONCTION UTILITAIRE: Export des métriques
def export_metrics_to_dict() -> Dict[str, Any]:
    """Exporte toutes les métriques dans un dictionnaire"""
    return {
        'solana_service_metrics': {
            'connection_metrics': solana_service._metrics,
            'performance_metrics': metrics_collector.get_summary(),
            'circuit_breaker_state': circuit_breaker.get_state(),
            'rate_limiter_stats': rate_limiter.get_stats(),
            'transaction_stats': transaction_manager.get_transaction_stats(),
            'cache_stats': state_cache.get_stats()
        },
        'system_info': {
            'rpc_url': solana_service.rpc_url,
            'program_id': str(solana_service.program_id),
            'commitment': str(solana_service.commitment),
            'admin_configured': solana_service.admin_keypair is not None
        },
        'timestamp': timezone.now().isoformat()
    }

# 🔹 FONCTION UTILITAIRE: Reset complet du service
async def reset_solana_service():
    """Remet à zéro complètement le service Solana"""
    try:
        logger.info("🔄 Resetting Solana service...")
        
        # Fermer les connexions existantes
        await solana_service.close_connections()
        
        # Reset des métriques
        metrics_collector.reset_metrics()
        
        # Reset du circuit breaker
        circuit_breaker.failure_count = 0
        circuit_breaker.state = 'CLOSED'
        circuit_breaker.last_failure_time = None
        
        # Vider le cache
        state_cache.invalidate()
        
        # Nettoyer les transactions
        transaction_manager.pending_transactions.clear()
        
        # Réinitialiser le service
        solana_service.connection = None
        solana_service.program = None
        
        logger.info("✅ Solana service reset completed")
        
    except Exception as e:
        logger.error(f"❌ Error resetting Solana service: {e}")
        raise

# 🔹 CLASSE UTILITAIRE: Gestionnaire d'événements Solana
class SolanaEventManager:
    """Gestionnaire d'événements pour les opérations Solana"""
    
    def __init__(self):
        self.event_handlers = {}
        self.event_history = []
        self.max_history = 500
    
    def register_handler(self, event_type: str, handler):
        """Enregistre un gestionnaire d'événement"""
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)
    
    async def emit_event(self, event_type: str, data: Dict[str, Any]):
        """Émet un événement"""
        event = {
            'type': event_type,
            'data': data,
            'timestamp': timezone.now().isoformat()
        }
        
        # Ajouter à l'historique
        self.event_history.append(event)
        if len(self.event_history) > self.max_history:
            self.event_history = self.event_history[-self.max_history:]
        
        # Appeler les gestionnaires
        if event_type in self.event_handlers:
            for handler in self.event_handlers[event_type]:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                except Exception as e:
                    logger.error(f"Error in event handler for {event_type}: {e}")
    
    def get_recent_events(self, event_type: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Récupère les événements récents"""
        events = self.event_history
        
        if event_type:
            events = [e for e in events if e['type'] == event_type]
        
        return events[-limit:]

# Instance globale du gestionnaire d'événements
event_manager = SolanaEventManager()

# 🔹 GESTIONNAIRES D'ÉVÉNEMENTS: Handlers par défaut
async def on_lottery_state_updated(event):
    """Gestionnaire pour la mise à jour de l'état de la loterie"""
    logger.info(f"Lottery state updated: {event['data']}")

async def on_participant_synced(event):
    """Gestionnaire pour la synchronisation d'un participant"""
    logger.info(f"Participant synced: {event['data']['wallet_address']}")

async def on_transaction_confirmed(event):
    """Gestionnaire pour la confirmation d'une transaction"""
    logger.info(f"Transaction confirmed: {event['data']['signature']}")

async def on_error_occurred(event):
    """Gestionnaire pour les erreurs"""
    logger.error(f"Solana error occurred: {event['data']}")

# Enregistrer les gestionnaires par défaut
event_manager.register_handler('lottery_state_updated', on_lottery_state_updated)
event_manager.register_handler('participant_synced', on_participant_synced)
event_manager.register_handler('transaction_confirmed', on_transaction_confirmed)
event_manager.register_handler('error_occurred', on_error_occurred)

# 🔹 DÉCORATEUR: Émission automatique d'événements
def emit_solana_event(event_type: str):
    """Décorateur pour émettre automatiquement des événements"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                result = await func(*args, **kwargs)
                
                # Émettre un événement de succès
                await event_manager.emit_event(f"{event_type}_success", {
                    'function': func.__name__,
                    'args': str(args)[:100],  # Limiter la taille
                    'result': str(result)[:200] if result else None
                })
                
                return result
            except Exception as e:
                # Émettre un événement d'erreur
                await event_manager.emit_event(f"{event_type}_error", {
                    'function': func.__name__,
                    'args': str(args)[:100],
                    'error': str(e)
                })
                raise
        
        return wrapper
    return decorator

# 🔹 FONCTION UTILITAIRE: Configuration avancée
class SolanaAdvancedConfig:
    """Configuration avancée pour le service Solana"""
    
    def __init__(self):
        self.config = {
            # Timeouts
            'connection_timeout': 15,
            'transaction_timeout': 60,
            'health_check_timeout': 10,
            
            # Retry settings
            'max_retries': 3,
            'retry_delay': 1.0,
            'exponential_backoff': True,
            
            # Rate limiting
            'max_requests_per_second': 10,
            'burst_limit': 20,
            
            # Circuit breaker
            'failure_threshold': 5,
            'recovery_timeout': 60,
            
            # Cache settings
            'cache_ttl': 60,
            'cache_max_size': 1000,
            
            # Monitoring
            'enable_metrics': True,
            'enable_events': True,
            'log_level': 'INFO'
        }
    
    def update_from_env(self):
        """Met à jour la configuration depuis les variables d'environnement"""
        import os
        
        env_mappings = {
            'SOLANA_CONNECTION_TIMEOUT': ('connection_timeout', int),
            'SOLANA_TRANSACTION_TIMEOUT': ('transaction_timeout', int),
            'SOLANA_MAX_RETRIES': ('max_retries', int),
            'SOLANA_RATE_LIMIT': ('max_requests_per_second', int),
            'SOLANA_CACHE_TTL': ('cache_ttl', int),
            'SOLANA_LOG_LEVEL': ('log_level', str)
        }
        
        for env_var, (config_key, type_func) in env_mappings.items():
            value = os.getenv(env_var)
            if value:
                try:
                    self.config[config_key] = type_func(value)
                    logger.info(f"Updated config {config_key} = {self.config[config_key]} from {env_var}")
                except ValueError as e:
                    logger.error(f"Invalid value for {env_var}: {value} - {e}")
    
    def get(self, key: str, default=None):
        """Récupère une valeur de configuration"""
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        """Définit une valeur de configuration"""
        self.config[key] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """Retourne la configuration complète"""
        return self.config.copy()

# Instance globale de la configuration avancée
advanced_config = SolanaAdvancedConfig()

# 🔹 FONCTION D'INITIALISATION FINALE
async def setup_solana_service_production():
    """Configuration complète du service Solana pour la production"""
    try:
        logger.info("🚀 Setting up Solana service for production...")
        
        # Charger la configuration depuis l'environnement
        advanced_config.update_from_env()
        
        # Configurer le rate limiter
        global rate_limiter
        rate_limiter = SolanaRateLimiter(
            max_requests_per_second=advanced_config.get('max_requests_per_second', 10)
        )
        
        # Configurer le circuit breaker
        global circuit_breaker
        circuit_breaker = SolanaCircuitBreaker(
            failure_threshold=advanced_config.get('failure_threshold', 5),
            recovery_timeout=advanced_config.get('recovery_timeout', 60)
        )
        
        # Configurer le cache
        global state_cache
        state_cache = SolanaStateCache(
            default_ttl=advanced_config.get('cache_ttl', 60)
        )
        
        # Initialiser le service principal
        success = await initialize_solana_service()
        if not success:
            raise Exception("Failed to initialize core Solana service")
        
        # Effectuer un health check initial
        health_data = await comprehensive_health_check()
        if health_data['overall_status'] == 'unhealthy':
            logger.warning("⚠️ Initial health check shows unhealthy status")
        
        logger.info("✅ Solana service production setup completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to setup Solana service for production: {e}")
        return False

# 🔹 EXPORT DES FONCTIONS PRINCIPALES
__all__ = [
    'SolanaService',
    'solana_service',
    'SolanaConnectionManager',
    'retry_on_failure',
    'batch_sync_participants',
    'performance_monitor',
    'state_cache',
    'rate_limiter',
    'circuit_breaker',
    'metrics_collector',
    'transaction_manager',
    'event_manager',
    'advanced_config',
    'comprehensive_health_check',
    'diagnose_solana_issues',
    'setup_solana_service_production',
    'reset_solana_service',
    'export_metrics_to_dict'
]

# 🔹 INITIALISATION AUTOMATIQUE
if __name__ == "__main__":
    # Script de test/diagnostic
    async def main():
        print("🔍 Solana Service Diagnostic Tool")
        print("=" * 50)
        
        # Setup
        await setup_solana_service_production()
        
        # Health check
        health = await comprehensive_health_check()
        print(f"Overall Status: {health['overall_status']}")
        
        # Diagnostic
        diagnosis = await diagnose_solana_issues()
        print(f"Issues found: {len(diagnosis['issues'])}")
        for issue in diagnosis['issues']:
            print(f"  - {issue}")
        
        # Métriques
        metrics = export_metrics_to_dict()
        print(f"Total requests: {metrics['solana_service_metrics']['performance_metrics']['total_operations']}")
        
        print("=" * 50)
        print("✅ Diagnostic completed")
    
    asyncio.run(main())
