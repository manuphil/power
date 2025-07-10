use anchor_lang::prelude::*;
use anchor_spl::token::{self, Token, TokenAccount, Mint};
use std::mem::size_of;

declare_id!("2wqFWNXDYT2Q71ToNFBqKpV4scKSi1cjMuqVcT2jgruV");

const SIGNATURE_MAX_LEN: usize = 88;
const MAX_PARTICIPANTS_PER_LOTTERY: u64 = 10000;
const HOURLY_JACKPOT_PERCENTAGE: u64 = 10; // 10%
const DAILY_JACKPOT_PERCENTAGE: u64 = 5;   // 5%
const BALL_DECIMALS: u8 = 8;
const TICKETS_PER_BALL: u64 = 10_000; // 1 ticket = 10,000 BALL tokens
const MIN_JACKPOT_AMOUNT: u64 = 1_000_000; // 0.001 SOL minimum
const MAX_JACKPOT_AMOUNT: u64 = 1_000_000_000_000; // 1000 SOL maximum

#[program]
pub mod lottery_solana {
    use super::*;

    pub fn initialize(
        ctx: Context<Initialize>,
        ball_token_mint: Pubkey,
        admin_authority: Pubkey,
    ) -> Result<()> {
        let lottery_state = &mut ctx.accounts.lottery_state;
        let clock = Clock::get()?;

        lottery_state.admin = admin_authority;
        lottery_state.ball_token_mint = ball_token_mint;
        lottery_state.hourly_jackpot_sol = 0;
        lottery_state.daily_jackpot_sol = 0;
        lottery_state.total_participants = 0;
        lottery_state.total_tickets = 0;
        lottery_state.last_hourly_draw = 0;
        lottery_state.last_daily_draw = 0;
        lottery_state.hourly_draw_count = 0;
        lottery_state.daily_draw_count = 0;
        lottery_state.is_paused = false;
        lottery_state.min_ticket_requirement = 1;
        lottery_state.max_tickets_per_wallet = 1000;
        lottery_state.fee_percentage = 250; // 2.5%
        lottery_state.treasury_balance = 0;
        lottery_state.total_volume_processed = 0;
        lottery_state.initialized_at = clock.unix_timestamp;
        lottery_state.last_updated = clock.unix_timestamp;
        lottery_state.emergency_stop = false;
        lottery_state.version = 1;

        emit!(ProgramInitialized {
            admin: admin_authority,
            ball_token_mint,
            timestamp: clock.unix_timestamp,
        });

        msg!("🎰 Lottery program initialized in production mode!");
        msg!("Admin: {}", admin_authority);
        msg!("BALL Token: {}", ball_token_mint);
        Ok(())
    }

    pub fn contribute_to_jackpot(
        ctx: Context<ContributeToJackpot>,
        sol_amount: u64,
        transaction_signature: String,
        source: ContributionSource,
    ) -> Result<()> {
        let lottery_state = &mut ctx.accounts.lottery_state;
        let clock = Clock::get()?;

        require!(!lottery_state.is_paused, LotteryError::ProgramPaused);
        require!(!lottery_state.emergency_stop, LotteryError::EmergencyStop);
        require!(sol_amount > 0, LotteryError::InvalidAmount);
        require!(
            transaction_signature.len() <= SIGNATURE_MAX_LEN,
            LotteryError::SignatureTooLong
        );

        // Calculer les contributions selon la source
        let (hourly_contribution, daily_contribution, fee_amount) = match source {
            ContributionSource::RaydiumSwap => {
                let fee = sol_amount * lottery_state.fee_percentage / 10000;
                let net_amount = sol_amount - fee;
                let hourly = net_amount * HOURLY_JACKPOT_PERCENTAGE / 100;
                let daily = net_amount * DAILY_JACKPOT_PERCENTAGE / 100;
                (hourly, daily, fee)
            },
            ContributionSource::DirectDeposit => {
                let hourly = sol_amount * HOURLY_JACKPOT_PERCENTAGE / 100;
                let daily = sol_amount * DAILY_JACKPOT_PERCENTAGE / 100;
                (hourly, daily, 0)
            },
            ContributionSource::Treasury => {
                (sol_amount / 2, sol_amount / 2, 0)
            },
        };

        // Vérifier les limites
        require!(
            lottery_state.hourly_jackpot_sol + hourly_contribution <= MAX_JACKPOT_AMOUNT,
            LotteryError::JackpotTooLarge
        );
        require!(
            lottery_state.daily_jackpot_sol + daily_contribution <= MAX_JACKPOT_AMOUNT,
            LotteryError::JackpotTooLarge
        );

        // Mettre à jour les jackpots
        lottery_state.hourly_jackpot_sol += hourly_contribution;
        lottery_state.daily_jackpot_sol += daily_contribution;
        lottery_state.treasury_balance += fee_amount;
        lottery_state.total_volume_processed += sol_amount;
        lottery_state.last_updated = clock.unix_timestamp;

        emit!(JackpotContribution {
            contributor: ctx.accounts.contributor.key(),
            sol_amount,
            hourly_contribution,
            daily_contribution,
            fee_amount,
            source,
            transaction_signature: transaction_signature.clone(),
            timestamp: clock.unix_timestamp,
        });

        msg!("💰 Production contribution: {} lamports", sol_amount);
        msg!("📈 Hourly jackpot: {} lamports", lottery_state.hourly_jackpot_sol);
        msg!("📈 Daily jackpot: {} lamports", lottery_state.daily_jackpot_sol);
        Ok(())
    }

    pub fn update_participant(
        ctx: Context<UpdateParticipant>,
        ball_balance: u64,
        _token_account_bump: u8,
    ) -> Result<()> {
        let participant = &mut ctx.accounts.participant;
        let lottery_state = &mut ctx.accounts.lottery_state;
        let clock = Clock::get()?;

        require!(!lottery_state.is_paused, LotteryError::ProgramPaused);
        require!(!lottery_state.emergency_stop, LotteryError::EmergencyStop);

        // Vérifier le compte de token BALL
        let token_account = &ctx.accounts.ball_token_account;
        require!(
            token_account.mint == lottery_state.ball_token_mint,
            LotteryError::InvalidTokenMint
        );
        require!(
            token_account.owner == ctx.accounts.user.key(),
            LotteryError::InvalidTokenOwner
        );

        // Vérifier que le solde correspond
        require!(
            token_account.amount >= ball_balance,
            LotteryError::InsufficientTokenBalance
        );

        let old_tickets = participant.tickets_count;
        let new_tickets = ball_balance / (TICKETS_PER_BALL * 10_u64.pow(BALL_DECIMALS as u32));

        // Vérifier les limites
        require!(
            new_tickets <= lottery_state.max_tickets_per_wallet,
            LotteryError::TooManyTickets
        );

        // Mettre à jour le participant
        participant.wallet = ctx.accounts.user.key();
        participant.ball_balance = ball_balance;
        participant.tickets_count = new_tickets;
        participant.is_eligible = new_tickets >= lottery_state.min_ticket_requirement;
        participant.last_updated = clock.unix_timestamp;
        participant.token_account = token_account.key();
        participant.participation_count += if old_tickets == 0 && new_tickets > 0 { 1 } else { 0 };

        // Mettre à jour les statistiques globales
        if old_tickets == 0 && new_tickets > 0 {
            lottery_state.total_participants += 1;
        } else if old_tickets > 0 && new_tickets == 0 {
            lottery_state.total_participants = lottery_state.total_participants.saturating_sub(1);
        }

        lottery_state.total_tickets = lottery_state.total_tickets
            .saturating_sub(old_tickets)
            .saturating_add(new_tickets);
        lottery_state.last_updated = clock.unix_timestamp;

        emit!(ParticipantUpdated {
            wallet: ctx.accounts.user.key(),
            ball_balance,
            tickets_count: new_tickets,
            is_eligible: participant.is_eligible,
            old_tickets,
            timestamp: clock.unix_timestamp,
        });

        msg!("👤 Participant updated: {}", ctx.accounts.user.key());
        msg!("🎫 Tickets: {} (from {} BALL)", new_tickets, ball_balance);
        Ok(())
    }

    pub fn create_lottery(
        ctx: Context<CreateLottery>,
        lottery_type: LotteryType,
        scheduled_time: i64,
    ) -> Result<()> {
        let lottery_state = &mut ctx.accounts.lottery_state;
        let lottery = &mut ctx.accounts.lottery;
        let clock = Clock::get()?;

        require!(!lottery_state.is_paused, LotteryError::ProgramPaused);
        require!(!lottery_state.emergency_stop, LotteryError::EmergencyStop);
        require!(scheduled_time > clock.unix_timestamp, LotteryError::InvalidScheduledTime);
        require!(
            lottery_state.total_participants > 0,
            LotteryError::NoParticipants
        );

        let (draw_id, jackpot_amount) = match lottery_type {
            LotteryType::Hourly => {
                lottery_state.hourly_draw_count += 1;
                require!(
                    lottery_state.hourly_jackpot_sol >= MIN_JACKPOT_AMOUNT,
                    LotteryError::InsufficientJackpot
                );
                (lottery_state.hourly_draw_count, lottery_state.hourly_jackpot_sol)
            },
            LotteryType::Daily => {
                lottery_state.daily_draw_count += 1;
                require!(
                    lottery_state.daily_jackpot_sol >= MIN_JACKPOT_AMOUNT,
                    LotteryError::InsufficientJackpot
                );
                (lottery_state.daily_draw_count, lottery_state.daily_jackpot_sol)
            },
        };

        // Initialiser la loterie
        lottery.draw_id = draw_id;
        lottery.lottery_type = lottery_type.clone();
        lottery.scheduled_time = scheduled_time;
        lottery.status = LotteryStatus::Pending;
        lottery.jackpot_amount = jackpot_amount;
        lottery.total_participants = lottery_state.total_participants;
        lottery.total_tickets = lottery_state.total_tickets;
        lottery.created_at = clock.unix_timestamp;
        lottery.executed_time = 0;
        lottery.winner = Pubkey::default();
        lottery.vrf_seed = 0;
        lottery.transaction_signature = String::new();
        lottery.slot_number = 0;
        lottery.payout_time = 0;
        lottery.gas_used = 0;
        lottery.block_hash = clock.slot;

        lottery_state.last_updated = clock.unix_timestamp;

        emit!(LotteryCreated {
            lottery_id: draw_id,
            lottery_type: lottery_type.clone(),
            scheduled_time,
            jackpot_amount,
            total_participants: lottery.total_participants,
            total_tickets: lottery.total_tickets,
            timestamp: clock.unix_timestamp,
        });

        msg!("🎰 PRODUCTION LOTTERY CREATED!");
        msg!("🆔 ID: {}", draw_id);
        msg!("💰 Jackpot: {} lamports", jackpot_amount);
        Ok(())
    }

    pub fn execute_lottery(
        ctx: Context<ExecuteLottery>,
        _lottery_type: LotteryType,
        _draw_id: u32,
        winner_wallet: Pubkey,
        vrf_seed: u64,
        transaction_signature: String,
    ) -> Result<()> {
        let lottery_state = &mut ctx.accounts.lottery_state;
        let lottery = &mut ctx.accounts.lottery;
        let clock = Clock::get()?;

        require!(!lottery_state.is_paused, LotteryError::ProgramPaused);
        require!(!lottery_state.emergency_stop, LotteryError::EmergencyStop);
        require!(lottery.status == LotteryStatus::Pending, LotteryError::InvalidLotteryStatus);
        require!(lottery.total_participants > 0, LotteryError::NoParticipants);
        require!(lottery.jackpot_amount > 0, LotteryError::InsufficientJackpot);
        require!(clock.unix_timestamp >= lottery.scheduled_time, LotteryError::TooEarly);
        require!(vrf_seed > 0, LotteryError::InvalidVRFSeed);

        // Vérifier que le gagnant est éligible
        let winner_participant = &ctx.accounts.winner_participant;
        require!(
            winner_participant.wallet == winner_wallet,
            LotteryError::InvalidWinner
        );
        require!(
            winner_participant.is_eligible,
            LotteryError::WinnerNotEligible
        );
        require!(
            winner_participant.tickets_count > 0,
            LotteryError::WinnerHasNoTickets
        );

        // Mettre à jour la loterie
        lottery.status = LotteryStatus::Processing;
        lottery.winner = winner_wallet;
        lottery.vrf_seed = vrf_seed;
        lottery.executed_time = clock.unix_timestamp;
        lottery.transaction_signature = transaction_signature.clone();
        lottery.slot_number = clock.slot;
        lottery.gas_used = 0; // À calculer si nécessaire

        // Réinitialiser le jackpot correspondant
        match lottery.lottery_type {
            LotteryType::Hourly => {
                lottery_state.hourly_jackpot_sol = 0;
                lottery_state.last_hourly_draw = clock.unix_timestamp;
            },
            LotteryType::Daily => {
                lottery_state.daily_jackpot_sol = 0;
                lottery_state.last_daily_draw = clock.unix_timestamp;
            },
        }

        lottery_state.last_updated = clock.unix_timestamp;
        emit!(LotteryExecuted {
            lottery_id: lottery.draw_id,
            lottery_type: lottery.lottery_type.clone(),
            winner: winner_wallet,
            jackpot_amount: lottery.jackpot_amount,
            total_participants: lottery.total_participants,
            total_tickets: lottery.total_tickets,
            winner_tickets: winner_participant.tickets_count,
            vrf_seed,
            transaction_signature,
            timestamp: clock.unix_timestamp,
            slot: clock.slot,
        });

        msg!("🎰 PRODUCTION LOTTERY EXECUTED!");
        msg!("🏆 Winner: {}", winner_wallet);
        msg!("💰 Jackpot: {} lamports", lottery.jackpot_amount);
        msg!("🎫 Winner tickets: {}", winner_participant.tickets_count);
        Ok(())
    }

    pub fn pay_winner(
        ctx: Context<PayWinner>,
        _lottery_type: LotteryType,
        _draw_id: u32,
    ) -> Result<()> {
        let lottery = &mut ctx.accounts.lottery;
        let clock = Clock::get()?;

        require!(!ctx.accounts.lottery_state.emergency_stop, LotteryError::EmergencyStop);
        require!(lottery.status == LotteryStatus::Processing, LotteryError::InvalidLotteryStatus);
        require!(lottery.winner == ctx.accounts.winner.key(), LotteryError::InvalidWinner);
        require!(lottery.jackpot_amount > 0, LotteryError::InsufficientJackpot);

        // Vérifier que le programme a suffisamment de fonds
        let program_balance = ctx.accounts.lottery_state.to_account_info().lamports();
        require!(
            program_balance >= lottery.jackpot_amount,
            LotteryError::InsufficientProgramBalance
        );

        let bump = ctx.bumps.lottery_state;
        let seeds = &[b"lottery_state".as_ref(), &[bump]];
        let signer_seeds = &[&seeds[..]];

        // Effectuer le transfert
        let cpi_ctx = CpiContext::new_with_signer(
            ctx.accounts.system_program.to_account_info(),
            anchor_lang::system_program::Transfer {
                from: ctx.accounts.lottery_state.to_account_info(),
                to: ctx.accounts.winner.to_account_info(),
            },
            signer_seeds,
        );

        anchor_lang::system_program::transfer(cpi_ctx, lottery.jackpot_amount)?;

        // Mettre à jour le statut
        lottery.status = LotteryStatus::Completed;
        lottery.payout_time = clock.unix_timestamp;

        emit!(WinnerPaid {
            lottery_id: lottery.draw_id,
            lottery_type: lottery.lottery_type.clone(),
            winner: ctx.accounts.winner.key(),
            amount: lottery.jackpot_amount,
            transaction_signature: lottery.transaction_signature.clone(),
            timestamp: clock.unix_timestamp,
        });

        msg!("💸 PRODUCTION WINNER PAID!");
        msg!("🏆 Winner: {}", ctx.accounts.winner.key());
        msg!("💰 Amount: {} lamports", lottery.jackpot_amount);
        Ok(())
    }

    pub fn emergency_pause(
        ctx: Context<AdminAction>,
        reason: String,
    ) -> Result<()> {
        let lottery_state = &mut ctx.accounts.lottery_state;
        let clock = Clock::get()?;

        lottery_state.emergency_stop = true;
        lottery_state.is_paused = true;
        lottery_state.last_updated = clock.unix_timestamp;

        emit!(EmergencyPause {
            admin: ctx.accounts.admin.key(),
            reason,
            timestamp: clock.unix_timestamp,
        });

        msg!("🚨 EMERGENCY PAUSE ACTIVATED!");
        Ok(())
    }

    pub fn emergency_resume(
        ctx: Context<AdminAction>,
        reason: String,
    ) -> Result<()> {
        let lottery_state = &mut ctx.accounts.lottery_state;
        let clock = Clock::get()?;

        lottery_state.emergency_stop = false;
        lottery_state.is_paused = false;
        lottery_state.last_updated = clock.unix_timestamp;

        emit!(EmergencyResume {
            admin: ctx.accounts.admin.key(),
            reason,
            timestamp: clock.unix_timestamp,
        });

        msg!("✅ EMERGENCY PAUSE LIFTED!");
        Ok(())
    }

    pub fn update_config(
        ctx: Context<AdminAction>,
        min_ticket_requirement: Option<u64>,
        max_tickets_per_wallet: Option<u64>,
        fee_percentage: Option<u64>,
    ) -> Result<()> {
        let lottery_state = &mut ctx.accounts.lottery_state;
        let clock = Clock::get()?;

        if let Some(min_tickets) = min_ticket_requirement {
            require!(min_tickets > 0 && min_tickets <= 100, LotteryError::InvalidConfig);
            lottery_state.min_ticket_requirement = min_tickets;
        }

        if let Some(max_tickets) = max_tickets_per_wallet {
            require!(max_tickets >= 1 && max_tickets <= 10000, LotteryError::InvalidConfig);
            lottery_state.max_tickets_per_wallet = max_tickets;
        }

        if let Some(fee) = fee_percentage {
            require!(fee <= 1000, LotteryError::InvalidConfig); // Max 10%
            lottery_state.fee_percentage = fee;
        }

        lottery_state.last_updated = clock.unix_timestamp;

        emit!(ConfigUpdated {
            admin: ctx.accounts.admin.key(),
            min_ticket_requirement,
            max_tickets_per_wallet,
            fee_percentage,
            timestamp: clock.unix_timestamp,
        });

        msg!("⚙️ Production config updated");
        Ok(())
    }

    pub fn withdraw_treasury(
        ctx: Context<WithdrawTreasury>,
        amount: u64,
    ) -> Result<()> {
        let clock = Clock::get()?;

        require!(amount > 0, LotteryError::InvalidAmount);
        require!(
            ctx.accounts.lottery_state.treasury_balance >= amount,
            LotteryError::InsufficientTreasuryBalance
        );

        let bump = ctx.bumps.lottery_state;
        let seeds = &[b"lottery_state".as_ref(), &[bump]];
        let signer_seeds = &[&seeds[..]];

        let cpi_ctx = CpiContext::new_with_signer(
            ctx.accounts.system_program.to_account_info(),
            anchor_lang::system_program::Transfer {
                from: ctx.accounts.lottery_state.to_account_info(),
                to: ctx.accounts.treasury_wallet.to_account_info(),
            },
            signer_seeds,
        );

        anchor_lang::system_program::transfer(cpi_ctx, amount)?;

        // Update treasury balance after transfer
        ctx.accounts.lottery_state.treasury_balance -= amount;
        ctx.accounts.lottery_state.last_updated = clock.unix_timestamp;

        emit!(TreasuryWithdrawal {
            admin: ctx.accounts.admin.key(),
            treasury_wallet: ctx.accounts.treasury_wallet.key(),
            amount,
            timestamp: clock.unix_timestamp,
        });

        msg!("💰 Treasury withdrawal: {} lamports", amount);
        Ok(())
    }

    pub fn get_lottery_state(
        ctx: Context<GetLotteryState>,
    ) -> Result<()> {
        let lottery_state = &ctx.accounts.lottery_state;

        msg!("📊 PRODUCTION LOTTERY STATE:");
        msg!("Admin: {}", lottery_state.admin);
        msg!("BALL Token: {}", lottery_state.ball_token_mint);
        msg!("Hourly Jackpot: {} lamports", lottery_state.hourly_jackpot_sol);
        msg!("Daily Jackpot: {} lamports", lottery_state.daily_jackpot_sol);
        msg!("Participants: {}", lottery_state.total_participants);
        msg!("Total Tickets: {}", lottery_state.total_tickets);
        msg!("Treasury Balance: {} lamports", lottery_state.treasury_balance);
        msg!("Total Volume: {} lamports", lottery_state.total_volume_processed);
        msg!("Emergency Stop: {}", lottery_state.emergency_stop);
        msg!("Paused: {}", lottery_state.is_paused);
        Ok(())
    }
}

// Helper functions
fn get_lottery_type_seed(lottery_type: &LotteryType) -> &'static [u8] {
    match lottery_type {
        LotteryType::Hourly => b"hourly",
        LotteryType::Daily => b"daily",
    }
}

fn _validate_vrf_seed(seed: u64, slot: u64, participants: u64) -> bool {
    // Validation basique du seed VRF
    seed > 0 && seed != slot && participants > 0
}

// Account structs
#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = admin,
        space = 8 + size_of::<LotteryState>(),
        seeds = [b"lottery_state"],
        bump
    )]
    pub lottery_state: Account<'info, LotteryState>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ContributeToJackpot<'info> {
    #[account(
        mut,
        seeds = [b"lottery_state"],
        bump
    )]
    pub lottery_state: Account<'info, LotteryState>,
    pub contributor: Signer<'info>,
}

#[derive(Accounts)]
pub struct UpdateParticipant<'info> {
    #[account(
        init_if_needed,
        payer = user,
        space = 8 + size_of::<Participant>(),
        seeds = [b"participant", user.key().as_ref()],
        bump
    )]
    pub participant: Account<'info, Participant>,
    #[account(
        mut,
        seeds = [b"lottery_state"],
        bump
    )]
    pub lottery_state: Account<'info, LotteryState>,
    #[account(mut)]
    pub user: Signer<'info>,
    #[account(
        constraint = ball_token_account.owner == user.key(),
        constraint = ball_token_account.mint == lottery_state.ball_token_mint
    )]
    pub ball_token_account: Account<'info, TokenAccount>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(lottery_type: LotteryType)]
pub struct CreateLottery<'info> {
    #[account(
        init,
        payer = admin,
        space = 8 + size_of::<Lottery>() + SIGNATURE_MAX_LEN,
        seeds = [
            b"lottery",
            get_lottery_type_seed(&lottery_type),
            &(match lottery_type {
                LotteryType::Hourly => lottery_state.hourly_draw_count + 1,
                LotteryType::Daily => lottery_state.daily_draw_count + 1,
            }).to_le_bytes()
        ],
        bump
    )]
    pub lottery: Account<'info, Lottery>,
    #[account(
        mut,
        seeds = [b"lottery_state"],
        bump
    )]
    pub lottery_state: Account<'info, LotteryState>,
    #[account(
        mut,
        constraint = admin.key() == lottery_state.admin
    )]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(lottery_type: LotteryType, draw_id: u32)]
pub struct ExecuteLottery<'info> {
    #[account(
        mut,
        seeds = [
            b"lottery",
            get_lottery_type_seed(&lottery_type),
            &draw_id.to_le_bytes()
        ],
        bump
    )]
    pub lottery: Account<'info, Lottery>,
    #[account(
        mut,
        seeds = [b"lottery_state"],
        bump
    )]
    pub lottery_state: Account<'info, LotteryState>,
    #[account(
        mut,
        constraint = admin.key() == lottery_state.admin
    )]
    pub admin: Signer<'info>,
    #[account(
        seeds = [b"participant", lottery.winner.as_ref()],
        bump,
        constraint = winner_participant.is_eligible,
        constraint = winner_participant.tickets_count > 0
    )]
    pub winner_participant: Account<'info, Participant>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(lottery_type: LotteryType, draw_id: u32)]
pub struct PayWinner<'info> {
    #[account(
        mut,
        seeds = [
            b"lottery",
            get_lottery_type_seed(&lottery_type),
            &draw_id.to_le_bytes()
        ],
        bump
    )]
    pub lottery: Account<'info, Lottery>,
    #[account(
        mut,
        seeds = [b"lottery_state"],
        bump
    )]
    pub lottery_state: Account<'info, LotteryState>,
    #[account(
        mut,
        constraint = winner.key() == lottery.winner
    )]
    /// CHECK: Winner address is validated against lottery.winner
    pub winner: UncheckedAccount<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct AdminAction<'info> {
    #[account(
        mut,
        seeds = [b"lottery_state"],
        bump,
        constraint = admin.key() == lottery_state.admin
    )]
    pub lottery_state: Account<'info, LotteryState>,
    pub admin: Signer<'info>,
}

#[derive(Accounts)]
pub struct WithdrawTreasury<'info> {
    #[account(
        mut,
        seeds = [b"lottery_state"],
        bump,
        constraint = admin.key() == lottery_state.admin
    )]
    pub lottery_state: Account<'info, LotteryState>,
    pub admin: Signer<'info>,
    #[account(mut)]
    /// CHECK: Treasury wallet address
    pub treasury_wallet: UncheckedAccount<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct GetLotteryState<'info> {
    #[account(
        seeds = [b"lottery_state"],
        bump
    )]
    pub lottery_state: Account<'info, LotteryState>,
}

// Data structs
#[account]
pub struct LotteryState {
    pub admin: Pubkey,                    // 32
    pub ball_token_mint: Pubkey,          // 32
    pub hourly_jackpot_sol: u64,          // 8
    pub daily_jackpot_sol: u64,           // 8
    pub total_participants: u64,          // 8
    pub total_tickets: u64,               // 8
    pub last_hourly_draw: i64,            // 8
    pub last_daily_draw: i64,             // 8
    pub hourly_draw_count: u32,           // 4
    pub daily_draw_count: u32,            // 4
    pub min_ticket_requirement: u64,      // 8
    pub max_tickets_per_wallet: u64,      // 8
    pub fee_percentage: u64,              // 8 (basis points, e.g., 250 = 2.5%)
    pub treasury_balance: u64,            // 8
    pub total_volume_processed: u64,      // 8
    pub initialized_at: i64,              // 8
    pub last_updated: i64,                // 8
    pub is_paused: bool,                  // 1
    pub emergency_stop: bool,             // 1
    pub version: u8,                      // 1
    // Total: 32+32+8+8+8+8+8+8+4+4+8+8+8+8+8+8+8+1+1+1 = 190 bytes
}

#[account]
pub struct Participant {
    pub wallet: Pubkey,                   // 32
    pub ball_balance: u64,                // 8
    pub tickets_count: u64,               // 8
    pub is_eligible: bool,                // 1
    pub last_updated: i64,                // 8
    pub token_account: Pubkey,            // 32
    pub participation_count: u64,         // 8
    pub total_winnings: u64,              // 8
    pub last_win_time: i64,               // 8
    // Total: 32+8+8+1+8+32+8+8+8 = 113 bytes
}

#[account]
pub struct Lottery {
    pub draw_id: u32,                     // 4
    pub lottery_type: LotteryType,        // 1 + alignment
    pub scheduled_time: i64,              // 8
    pub executed_time: i64,               // 8
    pub status: LotteryStatus,            // 1 + alignment
    pub jackpot_amount: u64,              // 8
    pub total_participants: u64,          // 8
    pub total_tickets: u64,               // 8
    pub winner: Pubkey,                   // 32
    pub vrf_seed: u64,                    // 8
    pub transaction_signature: String,    // 4 + SIGNATURE_MAX_LEN
    pub slot_number: u64,                 // 8
    pub payout_time: i64,                 // 8
    pub created_at: i64,                  // 8
    pub gas_used: u64,                    // 8
    pub block_hash: u64,                  // 8
    // Total: Variable due to String
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq)]
pub enum LotteryType {
    Hourly,
    Daily,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq)]
pub enum LotteryStatus {
    Pending,
    Processing,
    Completed,
    Cancelled,
    Failed,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq)]
pub enum ContributionSource {
    RaydiumSwap,
    DirectDeposit,
    Treasury,
}

// Events for production monitoring
#[event]
pub struct ProgramInitialized {
    pub admin: Pubkey,
    pub ball_token_mint: Pubkey,
    pub timestamp: i64,
}

#[event]
pub struct JackpotContribution {
    pub contributor: Pubkey,
    pub sol_amount: u64,
    pub hourly_contribution: u64,
    pub daily_contribution: u64,
    pub fee_amount: u64,
    pub source: ContributionSource,
    pub transaction_signature: String,
    pub timestamp: i64,
}

#[event]
pub struct ParticipantUpdated {
    pub wallet: Pubkey,
    pub ball_balance: u64,
    pub tickets_count: u64,
    pub is_eligible: bool,
    pub old_tickets: u64,
    pub timestamp: i64,
}

#[event]
pub struct LotteryCreated {
    pub lottery_id: u32,
    pub lottery_type: LotteryType,
    pub scheduled_time: i64,
    pub jackpot_amount: u64,
    pub total_participants: u64,
    pub total_tickets: u64,
    pub timestamp: i64,
}

#[event]
pub struct LotteryExecuted {
    pub lottery_id: u32,
    pub lottery_type: LotteryType,
    pub winner: Pubkey,
    pub jackpot_amount: u64,
    pub total_participants: u64,
    pub total_tickets: u64,
    pub winner_tickets: u64,
    pub vrf_seed: u64,
    pub transaction_signature: String,
    pub timestamp: i64,
    pub slot: u64,
}

#[event]
pub struct WinnerPaid {
    pub lottery_id: u32,
    pub lottery_type: LotteryType,
    pub winner: Pubkey,
    pub amount: u64,
    pub transaction_signature: String,
    pub timestamp: i64,
}

#[event]
pub struct EmergencyPause {
    pub admin: Pubkey,
    pub reason: String,
    pub timestamp: i64,
}

#[event]
pub struct EmergencyResume {
    pub admin: Pubkey,
    pub reason: String,
    pub timestamp: i64,
}

#[event]
pub struct ConfigUpdated {
    pub admin: Pubkey,
    pub min_ticket_requirement: Option<u64>,
    pub max_tickets_per_wallet: Option<u64>,
    pub fee_percentage: Option<u64>,
    pub timestamp: i64,
}

#[event]
pub struct TreasuryWithdrawal {
    pub admin: Pubkey,
    pub treasury_wallet: Pubkey,
    pub amount: u64,
    pub timestamp: i64,
}

// Production-ready error codes
#[error_code]
pub enum LotteryError {
    #[msg("The program is currently paused.")]
    ProgramPaused,
    #[msg("Emergency stop is active.")]
    EmergencyStop,
    #[msg("Invalid scheduled time.")]
    InvalidScheduledTime,
    #[msg("Invalid lottery status.")]
    InvalidLotteryStatus,
    #[msg("No participants.")]
    NoParticipants,
    #[msg("Insufficient jackpot.")]
    InsufficientJackpot,
    #[msg("Invalid winner.")]
    InvalidWinner,
    #[msg("Winner is not eligible.")]
    WinnerNotEligible,
    #[msg("Winner has no tickets.")]
    WinnerHasNoTickets,
    #[msg("Too early to execute the lottery.")]
    TooEarly,
    #[msg("Invalid amount.")]
    InvalidAmount,
    #[msg("Signature too long.")]
    SignatureTooLong,
    #[msg("Jackpot amount too large.")]
    JackpotTooLarge,
    #[msg("Invalid token mint.")]
    InvalidTokenMint,
    #[msg("Invalid token owner.")]
    InvalidTokenOwner,
    #[msg("Insufficient token balance.")]
    InsufficientTokenBalance,
    #[msg("Too many tickets for this wallet.")]
    TooManyTickets,
    #[msg("Invalid VRF seed.")]
    InvalidVRFSeed,
    #[msg("Insufficient program balance.")]
    InsufficientProgramBalance,
    #[msg("Invalid configuration parameter.")]
    InvalidConfig,
    #[msg("Insufficient treasury balance.")]
    InsufficientTreasuryBalance,
    #[msg("Unauthorized access.")]
    Unauthorized,
    #[msg("Account not found.")]
    AccountNotFound,
    #[msg("Invalid account data.")]
    InvalidAccountData,
    #[msg("Arithmetic overflow.")]
    ArithmeticOverflow,
    #[msg("Invalid instruction data.")]
    InvalidInstructionData,
    #[msg("Program account not rent exempt.")]
    NotRentExempt,
    #[msg("Invalid program state.")]
    InvalidProgramState,
}
