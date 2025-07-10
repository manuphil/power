import * as anchor from "@coral-xyz/anchor";
import { Program } from "@coral-xyz/anchor";
import { LotterySolana } from "../target/types/lottery_solana";
import { PublicKey, Keypair, LAMPORTS_PER_SOL } from "@solana/web3.js";
import { BN } from "@coral-xyz/anchor";
import { assert, expect } from "chai";

describe("Lottery Solana", () => {
  // Configure the client to use the local cluster
  anchor.setProvider(anchor.AnchorProvider.env());
  const program = anchor.workspace.LotterySolana as Program<LotterySolana>;
  const provider = anchor.getProvider();

  // Test variables
  let lotteryStatePda: PublicKey;
  let participant1Pda: PublicKey;
  let participant2Pda: PublicKey;
  let ballTokenMint: PublicKey;
  let hourlyLotteryPda: PublicKey;
  let dailyLotteryPda: PublicKey;
  
  // Test accounts
  let participant2 = Keypair.generate();
  let admin = provider.wallet.publicKey;

  before(async () => {
    console.log("🔧 Setting up test environment...");
    console.log("Admin wallet:", admin.toString());
    console.log("Participant2 wallet:", participant2.publicKey.toString());

    // Generate a mock BALL token mint
    ballTokenMint = Keypair.generate().publicKey;

    // Derive PDAs
    [lotteryStatePda] = PublicKey.findProgramAddressSync(
      [Buffer.from("lottery_state")],
      program.programId
    );

    [participant1Pda] = PublicKey.findProgramAddressSync(
      [Buffer.from("participant"), admin.toBuffer()],
      program.programId
    );

    [participant2Pda] = PublicKey.findProgramAddressSync(
      [Buffer.from("participant"), participant2.publicKey.toBuffer()],
      program.programId
    );

    // Airdrop SOL to participant2 for testing
    const signature = await provider.connection.requestAirdrop(
      participant2.publicKey,
      2 * LAMPORTS_PER_SOL
    );
    await provider.connection.confirmTransaction(signature);

    console.log("✅ Test setup complete");
  });

  it("Should initialize the lottery program", async () => {
    console.log("🎰 Initializing Lottery Program...");

    try {
      // Check if already initialized
      const existingState = await program.account.lotteryState.fetch(lotteryStatePda);
      console.log("⚠️ Lottery already initialized, skipping...");
      return;
    } catch (error) {
      console.log("🆕 Lottery not initialized, proceeding...");
    }

    // Initialize the lottery
    const txHash = await program.methods
      .initialize(ballTokenMint)
      .accounts({
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    console.log(`✅ Initialize tx: ${txHash}`);

    // Fetch and verify the created account
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
    
    console.log("🎰 Lottery State:", {
      admin: lotteryState.admin.toString(),
      ballTokenMint: lotteryState.ballTokenMint.toString(),
      totalParticipants: lotteryState.totalParticipants.toString(),
      totalTickets: lotteryState.totalTickets.toString(),
      isPaused: lotteryState.isPaused,
    });

    // Assertions
    assert(lotteryState.admin.equals(admin));
    assert(lotteryState.ballTokenMint.equals(ballTokenMint));
    assert(lotteryState.totalParticipants.eq(new BN(0)));
    assert(lotteryState.totalTickets.eq(new BN(0)));
    assert(lotteryState.isPaused === false);
  });

  it("Should contribute to jackpot", async () => {
    console.log("💰 Contributing to jackpot...");

    const contributionAmount = new BN(1000000); // 0.001 SOL in lamports
    const transactionSignature = "test_tx_signature_123";

    const txHash = await program.methods
      .contributeToJackpot(contributionAmount, transactionSignature)
      .accounts({
        lotteryState: lotteryStatePda,
        contributor: admin,
      })
      .rpc();

    console.log(`✅ Contribute tx: ${txHash}`);

    // Fetch updated state
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
    
    console.log("💰 Jackpots after contribution:", {
      hourly: lotteryState.hourlyJackpotSol.toString(),
      daily: lotteryState.dailyJackpotSol.toString(),
    });

    // Verify contributions (10% hourly, 5% daily)
    const expectedHourly = contributionAmount.mul(new BN(10)).div(new BN(100));
    const expectedDaily = contributionAmount.mul(new BN(5)).div(new BN(100));

    assert(lotteryState.hourlyJackpotSol.eq(expectedHourly));
    assert(lotteryState.dailyJackpotSol.eq(expectedDaily));
  });

  it("Should update participant with BALL tokens", async () => {
    console.log("👤 Updating participant 1...");

    const ballBalance = new BN("50000000000000"); // 500,000 BALL tokens (with 8 decimals)
    const expectedTickets = ballBalance.div(new BN("10000000000")); // 1 ticket = 10,000 BALL

    const txHash = await program.methods
      .updateParticipant(ballBalance)
      .accounts({
        participant: participant1Pda,
        lotteryState: lotteryStatePda,
        user: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    console.log(`✅ Update participant tx: ${txHash}`);

    // Fetch participant and lottery state
    const participant = await program.account.participant.fetch(participant1Pda);
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);

    console.log("👤 Participant data:", {
      wallet: participant.wallet.toString(),
      ballBalance: participant.ballBalance.toString(),
      tickets: participant.ticketsCount.toString(),
      isEligible: participant.isEligible,
    });

    // Assertions
    assert(participant.wallet.equals(admin));
    assert(participant.ballBalance.eq(ballBalance));
    assert(participant.ticketsCount.eq(expectedTickets));
    assert(participant.isEligible === true);
    assert(lotteryState.totalParticipants.eq(new BN(1)));
    assert(lotteryState.totalTickets.eq(expectedTickets));
  });

  it("Should add second participant", async () => {
    console.log("👤 Adding participant 2...");

    const ballBalance = new BN("30000000000000"); // 300,000 BALL tokens
    const expectedTickets = ballBalance.div(new BN("10000000000")); // 30 tickets

    const txHash = await program.methods
      .updateParticipant(ballBalance)
      .accounts({
        participant: participant2Pda,
        lotteryState: lotteryStatePda,
        user: participant2.publicKey,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([participant2])
      .rpc();

    console.log(`✅ Add participant 2 tx: ${txHash}`);

    // Fetch updated state
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
    const participant = await program.account.participant.fetch(participant2Pda);

    console.log("📊 Updated totals:", {
      totalParticipants: lotteryState.totalParticipants.toString(),
      totalTickets: lotteryState.totalTickets.toString(),
    });

    // Assertions
    assert(lotteryState.totalParticipants.eq(new BN(2)));
    assert(participant.ticketsCount.eq(expectedTickets));
  });

  it("Should create hourly lottery", async () => {
    console.log("🎰 Creating hourly lottery...");

    const scheduledTime = new BN(Math.floor(Date.now() / 1000) + 3600); // 1 hour from now
    const lotteryType = { hourly: {} };

    // Derive hourly lottery PDA
    [hourlyLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("hourly"),
        new BN(1).toArrayLike(Buffer, "le", 4), // draw_id = 1
      ],
      program.programId
    );

    const txHash = await program.methods
      .createLottery(lotteryType, scheduledTime)
      .accounts({
        lottery: hourlyLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    console.log(`✅ Create hourly lottery tx: ${txHash}`);

    // Fetch created lottery
    const lottery = await program.account.lottery.fetch(hourlyLotteryPda);
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);

    console.log("🎰 Hourly Lottery:", {
      drawId: lottery.drawId,
      scheduledTime: lottery.scheduledTime.toString(),
      jackpotAmount: lottery.jackpotAmount.toString(),
      totalParticipants: lottery.totalParticipants.toString(),
      status: lottery.status,
    });

    // Assertions
    assert(lottery.drawId === 1);
    assert(lottery.scheduledTime.eq(scheduledTime));
    assert(lottery.jackpotAmount.gt(new BN(0))); // Should have jackpot from contributions
    assert(lottery.totalParticipants.eq(new BN(2)));
    assert(lotteryState.hourlyDrawCount === 1);
  });

  it("Should create daily lottery", async () => {
    console.log("🎰 Creating daily lottery...");

    const scheduledTime = new BN(Math.floor(Date.now() / 1000) + 86400); // 1 day from now
    const lotteryType = { daily: {} };

    // Derive daily lottery PDA
    [dailyLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("daily"),
        new BN(1).toArrayLike(Buffer, "le", 4), // draw_id = 1
      ],
      program.programId
    );

    const txHash = await program.methods
      .createLottery(lotteryType, scheduledTime)
      .accounts({
        lottery: dailyLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    console.log(`✅ Create daily lottery tx: ${txHash}`);

    // Fetch created lottery
    const lottery = await program.account.lottery.fetch(dailyLotteryPda);
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);

    console.log("🎰 Daily Lottery:", {
      drawId: lottery.drawId,
      scheduledTime: lottery.scheduledTime.toString(),
      jackpotAmount: lottery.jackpotAmount.toString(),
      totalParticipants: lottery.totalParticipants.toString(),
      status: lottery.status,
    });

    // Assertions
    assert(lottery.drawId === 1);
    assert(lottery.scheduledTime.eq(scheduledTime));
    assert(lottery.jackpotAmount.gt(new BN(0)));
    assert(lotteryState.dailyDrawCount === 1);
  });

  it("Should execute hourly lottery", async () => {
    console.log("🎰 Executing hourly lottery...");

    // First, update the lottery to have a past scheduled time
    const lottery = await program.account.lottery.fetch(hourlyLotteryPda);
    const pastTime = new BN(Math.floor(Date.now() / 1000) - 1); // 1 second ago

    // We need to create a new lottery with past time for testing
    const lotteryType = { hourly: {} };
    const winnerWallet = admin; // Admin wins for testing
    const vrfSeed = new BN(12345);
    const transactionSignature = "execution_tx_signature_456";

    // For testing, we'll modify the scheduled time by creating a new lottery
    const newScheduledTime = pastTime;
    
    // Create a new lottery with past time
    const [testLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("hourly"),
        new BN(2).toArrayLike(Buffer, "le", 4), // draw_id = 2
      ],
      program.programId
    );

    // Create lottery with past time
    await program.methods
      .createLottery(lotteryType, newScheduledTime)
      .accounts({
        lottery: testLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // Now execute it
    const txHash = await program.methods
      .executeLottery(lotteryType, 2, winnerWallet, vrfSeed, transactionSignature)
      .accounts({
        lottery: testLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    console.log(`✅ Execute lottery tx: ${txHash}`);

    // Fetch updated lottery
    const executedLottery = await program.account.lottery.fetch(testLotteryPda);
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);

    console.log("🏆 Executed Lottery:", {
      winner: executedLottery.winner.toString(),
      status: executedLottery.status,
      vrfSeed: executedLottery.vrfSeed.toString(),
      executedTime: executedLottery.executedTime.toString(),
    });

    // Assertions
    assert(executedLottery.winner.equals(winnerWallet));
    assert(executedLottery.vrfSeed.eq(vrfSeed));
    assert(executedLottery.executedTime.gt(new BN(0)));
      // Note: Status should be Processing, not Completed yet
      assert(JSON.stringify(executedLottery.status) === JSON.stringify({ processing: {} }));
      
      // Hourly jackpot should be reset to 0
      assert(lotteryState.hourlyJackpotSol.eq(new BN(0)));
    });
  
    it("Should pay the winner", async () => {
      console.log("💸 Paying the winner...");
  
      // Get the executed lottery (draw_id = 2)
      const [testLotteryPda] = PublicKey.findProgramAddressSync(
        [
          Buffer.from("lottery"),
          Buffer.from("hourly"),
          new BN(2).toArrayLike(Buffer, "le", 4),
        ],
        program.programId
      );
  
      const lotteryType = { hourly: {} };
      const drawId = 2;
  
      // Get lottery state before payment
      const lotteryBefore = await program.account.lottery.fetch(testLotteryPda);
      const jackpotAmount = lotteryBefore.jackpotAmount;
  
      // Get winner's balance before payment
      const winnerBalanceBefore = await provider.connection.getBalance(admin);
  
      const txHash = await program.methods
        .payWinner(lotteryType, drawId)
        .accounts({
          lottery: testLotteryPda,
          lotteryState: lotteryStatePda,
          winner: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
  
      console.log(`✅ Pay winner tx: ${txHash}`);
  
      // Fetch updated lottery
      const paidLottery = await program.account.lottery.fetch(testLotteryPda);
      
      // Get winner's balance after payment
      const winnerBalanceAfter = await provider.connection.getBalance(admin);
  
      console.log("💰 Payment Details:", {
        jackpotAmount: jackpotAmount.toString(),
        winnerBalanceBefore: winnerBalanceBefore,
        winnerBalanceAfter: winnerBalanceAfter,
        balanceIncrease: winnerBalanceAfter - winnerBalanceBefore,
        status: paidLottery.status,
        payoutTime: paidLottery.payoutTime.toString(),
      });
  
      // Assertions
      assert(JSON.stringify(paidLottery.status) === JSON.stringify({ completed: {} }));
      assert(paidLottery.payoutTime.gt(new BN(0)));
      // Note: Balance check might be affected by transaction fees
      assert(winnerBalanceAfter > winnerBalanceBefore);
    });
  
    it("Should update configuration", async () => {
      console.log("⚙️ Updating configuration...");
  
      const newMinTicketRequirement = new BN(5);
  
      const txHash = await program.methods
        .updateConfig(newMinTicketRequirement)
        .accounts({
          lotteryState: lotteryStatePda,
          admin: admin,
        })
        .rpc();
  
      console.log(`✅ Update config tx: ${txHash}`);
  
      // Fetch updated state
      const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
  
      console.log("⚙️ Updated Config:", {
        minTicketRequirement: lotteryState.minTicketRequirement.toString(),
      });
  
      // Assertions
      assert(lotteryState.minTicketRequirement.eq(newMinTicketRequirement));
    });
  
    it("Should toggle pause state", async () => {
      console.log("⏸️ Testing pause functionality...");
  
      // Get initial state
      const initialState = await program.account.lotteryState.fetch(lotteryStatePda);
      const initialPauseState = initialState.isPaused;
  
      // Toggle pause
      const txHash = await program.methods
        .togglePause()
        .accounts({
          lotteryState: lotteryStatePda,
          admin: admin,
        })
        .rpc();
  
      console.log(`✅ Toggle pause tx: ${txHash}`);
  
      // Fetch updated state
      const updatedState = await program.account.lotteryState.fetch(lotteryStatePda);
  
      console.log("⏸️ Pause State:", {
        before: initialPauseState,
        after: updatedState.isPaused,
      });
  
      // Assertions
      assert(updatedState.isPaused === !initialPauseState);
  
      // Toggle back to original state
      await program.methods
        .togglePause()
        .accounts({
          lotteryState: lotteryStatePda,
          admin: admin,
        })
        .rpc();
  
      const finalState = await program.account.lotteryState.fetch(lotteryStatePda);
      assert(finalState.isPaused === initialPauseState);
    });
  
    it("Should handle participant with insufficient tickets", async () => {
      console.log("👤 Testing participant with insufficient tickets...");
  
      const lowBalance = new BN("5000000000"); // 50 BALL tokens (less than 10,000 needed for 1 ticket)
      const participant3 = Keypair.generate();
  
      // Airdrop SOL to participant3
      const signature = await provider.connection.requestAirdrop(
        participant3.publicKey,
        LAMPORTS_PER_SOL
      );
      await provider.connection.confirmTransaction(signature);
  
      const [participant3Pda] = PublicKey.findProgramAddressSync(
        [Buffer.from("participant"), participant3.publicKey.toBuffer()],
        program.programId
      );
  
      const txHash = await program.methods
        .updateParticipant(lowBalance)
        .accounts({
          participant: participant3Pda,
          lotteryState: lotteryStatePda,
          user: participant3.publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .signers([participant3])
        .rpc();
  
      console.log(`✅ Update low-balance participant tx: ${txHash}`);
  
      // Fetch participant
      const participant = await program.account.participant.fetch(participant3Pda);
  
      console.log("👤 Low-balance Participant:", {
        ballBalance: participant.ballBalance.toString(),
        tickets: participant.ticketsCount.toString(),
        isEligible: participant.isEligible,
      });
  
      // Assertions
      assert(participant.ballBalance.eq(lowBalance));
      assert(participant.ticketsCount.eq(new BN(0))); // 0 tickets
      assert(participant.isEligible === false); // Not eligible
    });
  
    it("Should fail when non-admin tries admin functions", async () => {
      console.log("🚫 Testing unauthorized access...");
  
      const nonAdmin = Keypair.generate();
      
      // Airdrop SOL to non-admin
      const signature = await provider.connection.requestAirdrop(
        nonAdmin.publicKey,
        LAMPORTS_PER_SOL
      );
      await provider.connection.confirmTransaction(signature);
  
      // Try to update config as non-admin (should fail)
      try {
        await program.methods
          .updateConfig(new BN(10))
          .accounts({
            lotteryState: lotteryStatePda,
            admin: nonAdmin.publicKey,
          })
          .signers([nonAdmin])
          .rpc();
        
        assert.fail("Should have failed with unauthorized access");
      } catch (error) {
        console.log("✅ Correctly rejected unauthorized access");
        assert(error.message.includes("A has_one constraint was violated"));
      }
    });
  
    it("Should fail to execute lottery too early", async () => {
      console.log("⏰ Testing early execution prevention...");
  
      const futureTime = new BN(Math.floor(Date.now() / 1000) + 3600); // 1 hour from now
      const lotteryType = { hourly: {} };
  
      // Create lottery with future time
      const [futureLotteryPda] = PublicKey.findProgramAddressSync(
        [
          Buffer.from("lottery"),
          Buffer.from("hourly"),
          new BN(3).toArrayLike(Buffer, "le", 4),
        ],
        program.programId
      );
  
      await program.methods
        .createLottery(lotteryType, futureTime)
        .accounts({
          lottery: futureLotteryPda,
          lotteryState: lotteryStatePda,
          admin: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
  
      // Try to execute too early (should fail)
      try {
        await program.methods
          .executeLottery(
            lotteryType,
            3,
            admin,
            new BN(54321),
            "early_execution_test"
          )
          .accounts({
            lottery: futureLotteryPda,
            lotteryState: lotteryStatePda,
            admin: admin,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc();
        
        assert.fail("Should have failed with TooEarly error");
      } catch (error) {
        console.log("✅ Correctly prevented early execution");
        assert(error.message.includes("TooEarly") || error.message.includes("6008"));
      }
    });
  
    it("Should get final lottery state", async () => {
      console.log("📊 Getting final lottery state...");
  
      // Use the get_lottery_state instruction
      const txHash = await program.methods
        .getLotteryState()
        .accounts({
          lotteryState: lotteryStatePda,
        })
        .rpc();
  
      console.log(`✅ Get lottery state tx: ${txHash}`);
  
      // Fetch the final state directly
      const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
  
      console.log("📊 FINAL LOTTERY STATE:", {
        admin: lotteryState.admin.toString(),
        ballTokenMint: lotteryState.ballTokenMint.toString(),
        hourlyJackpot: lotteryState.hourlyJackpotSol.toString(),
        dailyJackpot: lotteryState.dailyJackpotSol.toString(),
        totalParticipants: lotteryState.totalParticipants.toString(),
        totalTickets: lotteryState.totalTickets.toString(),
        hourlyDrawCount: lotteryState.hourlyDrawCount,
        dailyDrawCount: lotteryState.dailyDrawCount,
        minTicketRequirement: lotteryState.minTicketRequirement.toString(),
        isPaused: lotteryState.isPaused,
        lastHourlyDraw: lotteryState.lastHourlyDraw.toString(),
        lastDailyDraw: lotteryState.lastDailyDraw.toString(),
      });
  
      // Final comprehensive assertions
      assert(lotteryState.admin.equals(admin));
      assert(lotteryState.ballTokenMint.equals(ballTokenMint));
      assert(lotteryState.totalParticipants.gte(new BN(2))); // At least 2 participants
      assert(lotteryState.totalTickets.gt(new BN(0))); // Some tickets exist
      assert(lotteryState.hourlyDrawCount >= 3); // At least 3 hourly draws created
      assert(lotteryState.dailyDrawCount >= 1); // At least 1 daily draw created
      assert(lotteryState.minTicketRequirement.eq(new BN(5))); // Updated config
      assert(lotteryState.isPaused === false); // Should be unpaused
      assert(lotteryState.lastHourlyDraw.gt(new BN(0))); // Should have executed an hourly draw
  
      console.log("✅ All tests completed successfully!");
    });
  
    // Additional edge case tests
    it("Should handle participant balance updates correctly", async () => {
      console.log("🔄 Testing participant balance updates...");
  
      const initialBalance = new BN("20000000000000"); // 200,000 BALL
      const updatedBalance = new BN("40000000000000"); // 400,000 BALL
  
      // Update participant1 with new balance
      await program.methods
        .updateParticipant(updatedBalance)
        .accounts({
          participant: participant1Pda,
          lotteryState: lotteryStatePda,
          user: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
  
      const participant = await program.account.participant.fetch(participant1Pda);
      const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
  
      console.log("🔄 Updated Participant:", {
        ballBalance: participant.ballBalance.toString(),
        tickets: participant.ticketsCount.toString(),
        totalTickets: lotteryState.totalTickets.toString(),
      });
  
      const expectedTickets = updatedBalance.div(new BN("10000000000"));
      assert(participant.ballBalance.eq(updatedBalance));
      assert(participant.ticketsCount.eq(expectedTickets));
    });
  
    it("Should handle multiple contributions to jackpot", async () => {
      console.log("💰 Testing multiple jackpot contributions...");
  
      const contribution1 = new BN(500000); // 0.0005 SOL
      const contribution2 = new BN(750000); // 0.00075 SOL
  
      // Get initial jackpot amounts
      const initialState = await program.account.lotteryState.fetch(lotteryStatePda);
      const initialHourly = initialState.hourlyJackpotSol;
      const initialDaily = initialState.dailyJackpotSol;
  
      // First contribution
      await program.methods
        .contributeToJackpot(contribution1, "multi_contrib_1")
        .accounts({
          lotteryState: lotteryStatePda,
          contributor: admin,
        })
        .rpc();
  
      // Second contribution
      await program.methods
        .contributeToJackpot(contribution2, "multi_contrib_2")
        .accounts({
          lotteryState: lotteryStatePda,
          contributor: admin,
        })
        .rpc();
  
      const finalState = await program.account.lotteryState.fetch(lotteryStatePda);
  
      const totalContribution = contribution1.add(contribution2);
      const expectedHourlyIncrease = totalContribution.mul(new BN(10)).div(new BN(100));
      const expectedDailyIncrease = totalContribution.mul(new BN(5)).div(new BN(100));
  
      console.log("💰 Multiple Contributions Result:", {
        totalContribution: totalContribution.toString(),
        hourlyIncrease: expectedHourlyIncrease.toString(),
        dailyIncrease: expectedDailyIncrease.toString(),
        finalHourly: finalState.hourlyJackpotSol.toString(),
        finalDaily: finalState.dailyJackpotSol.toString(),
      });
  
      assert(finalState.hourlyJackpotSol.eq(initialHourly.add(expectedHourlyIncrease)));
      assert(finalState.dailyJackpotSol.eq(initialDaily.add(expectedDailyIncrease)));
    });
  
     // Test error cases
  it("Should fail when trying to contribute while paused", async () => {
    console.log("⏸️ Testing contribution while paused...");

    // Pause the program
    await program.methods
      .togglePause()
      .accounts({
        lotteryState: lotteryStatePda,
        admin: admin,
      })
      .rpc();

    // Try to contribute while paused (should fail)
    try {
      await program.methods
        .contributeToJackpot(new BN(100000), "paused_contrib")
        .accounts({
          lotteryState: lotteryStatePda,
          contributor: admin,
        })
        .rpc();
      
      assert.fail("Should have failed with ProgramPaused error");
    } catch (error) {
      console.log("✅ Correctly prevented contribution while paused");
      assert(error.message.includes("ProgramPaused") || error.message.includes("6000"));
    }

    // Unpause for other tests
    await program.methods
      .togglePause()
      .accounts({
        lotteryState: lotteryStatePda,
        admin: admin,
      })
      .rpc();
  });

  it("Should fail when trying to create lottery while paused", async () => {
    console.log("⏸️ Testing lottery creation while paused...");

    // Pause the program
    await program.methods
      .togglePause()
      .accounts({
        lotteryState: lotteryStatePda,
        admin: admin,
      })
      .rpc();

    const [pausedLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("hourly"),
        new BN(99).toArrayLike(Buffer, "le", 4),
      ],
      program.programId
    );

    // Try to create lottery while paused (should fail)
    try {
      await program.methods
        .createLottery(
          { hourly: {} },
          new BN(Math.floor(Date.now() / 1000) + 3600)
        )
        .accounts({
          lottery: pausedLotteryPda,
          lotteryState: lotteryStatePda,
          admin: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      
      assert.fail("Should have failed with ProgramPaused error");
    } catch (error) {
      console.log("✅ Correctly prevented lottery creation while paused");
      assert(error.message.includes("ProgramPaused") || error.message.includes("6000"));
    }

    // Unpause for other tests
    await program.methods
      .togglePause()
      .accounts({
        lotteryState: lotteryStatePda,
        admin: admin,
      })
      .rpc();
  });

  it("Should fail when trying to pay wrong winner", async () => {
    console.log("🚫 Testing wrong winner payment...");

    // Create and execute a new lottery
    const [wrongWinnerLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("hourly"),
        new BN(4).toArrayLike(Buffer, "le", 4),
      ],
      program.programId
    );

    const pastTime = new BN(Math.floor(Date.now() / 1000) - 1);
    const lotteryType = { hourly: {} };

    // Create lottery
    await program.methods
      .createLottery(lotteryType, pastTime)
      .accounts({
        lottery: wrongWinnerLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // Execute lottery with admin as winner
    await program.methods
      .executeLottery(
        lotteryType,
        4,
        admin,
        new BN(99999),
        "wrong_winner_test"
      )
      .accounts({
        lottery: wrongWinnerLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // Try to pay different winner (should fail)
    try {
      await program.methods
        .payWinner(lotteryType, 4)
        .accounts({
          lottery: wrongWinnerLotteryPda,
          lotteryState: lotteryStatePda,
          winner: participant2.publicKey, // Wrong winner
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      
      assert.fail("Should have failed with InvalidWinner error");
    } catch (error) {
      console.log("✅ Correctly prevented wrong winner payment");
      assert(error.message.includes("InvalidWinner") || error.message.includes("6003"));
    }
  });

  it("Should fail when trying to execute already executed lottery", async () => {
    console.log("🚫 Testing double execution prevention...");

    // Try to execute the already executed lottery (draw_id = 2)
    try {
      await program.methods
        .executeLottery(
          { hourly: {} },
          2,
          admin,
          new BN(11111),
          "double_execution_test"
        )
        .accounts({
          lottery: PublicKey.findProgramAddressSync(
            [
              Buffer.from("lottery"),
              Buffer.from("hourly"),
              new BN(2).toArrayLike(Buffer, "le", 4),
            ],
            program.programId
          )[0],
          lotteryState: lotteryStatePda,
          admin: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      
      assert.fail("Should have failed with InvalidLotteryStatus error");
    } catch (error) {
      console.log("✅ Correctly prevented double execution");
      assert(error.message.includes("InvalidLotteryStatus") || error.message.includes("6006"));
    }
  });

  it("Should handle participant removal (zero balance)", async () => {
    console.log("👤 Testing participant removal...");

    // Update participant2 to have zero balance
    const zeroBalance = new BN(0);

    const txHash = await program.methods
      .updateParticipant(zeroBalance)
      .accounts({
        participant: participant2Pda,
        lotteryState: lotteryStatePda,
        user: participant2.publicKey,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([participant2])
      .rpc();

    console.log(`✅ Remove participant tx: ${txHash}`);

    const participant = await program.account.participant.fetch(participant2Pda);
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);

    console.log("👤 Removed Participant:", {
      ballBalance: participant.ballBalance.toString(),
      tickets: participant.ticketsCount.toString(),
      isEligible: participant.isEligible,
      totalParticipants: lotteryState.totalParticipants.toString(),
    });

    // Assertions
    assert(participant.ballBalance.eq(zeroBalance));
    assert(participant.ticketsCount.eq(new BN(0)));
    assert(participant.isEligible === false);
    // Total participants should decrease
    assert(lotteryState.totalParticipants.lt(new BN(3)));
  });

  it("Should create lottery with invalid scheduled time", async () => {
    console.log("⏰ Testing invalid scheduled time...");

    const pastTime = new BN(Math.floor(Date.now() / 1000) - 3600); // 1 hour ago
    const [invalidTimeLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("daily"),
        new BN(2).toArrayLike(Buffer, "le", 4),
      ],
      program.programId
    );

    try {
      await program.methods
        .createLottery({ daily: {} }, pastTime)
        .accounts({
          lottery: invalidTimeLotteryPda,
          lotteryState: lotteryStatePda,
          admin: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      
      assert.fail("Should have failed with InvalidScheduledTime error");
    } catch (error) {
      console.log("✅ Correctly prevented invalid scheduled time");
      assert(error.message.includes("InvalidScheduledTime") || error.message.includes("6007"));
    }
  });

  it("Should test event emissions", async () => {
    console.log("📡 Testing event emissions...");

    // Create a listener for events
    const eventListener = program.addEventListener("JackpotContribution", (event, slot) => {
      console.log("🎉 JackpotContribution Event:", {
        contributor: event.contributor.toString(),
        solAmount: event.solAmount.toString(),
        hourlyContribution: event.hourlyContribution.toString(),
        dailyContribution: event.dailyContribution.toString(),
        slot: slot,
      });
    });

    // Make a contribution to trigger event
    await program.methods
      .contributeToJackpot(new BN(200000), "event_test_signature")
      .accounts({
        lotteryState: lotteryStatePda,
        contributor: admin,
      })
      .rpc();

    // Wait a bit for event processing
    await new Promise(resolve => setTimeout(resolve, 1000));

    // Remove event listener
    await program.removeEventListener(eventListener);

    console.log("✅ Event emission test completed");
  });

  it("Should test comprehensive lottery flow", async () => {
    console.log("🎯 Testing complete lottery flow...");

    // 1. Add more contributions to build jackpot
    await program.methods
      .contributeToJackpot(new BN(2000000), "flow_test_1")
      .accounts({
        lotteryState: lotteryStatePda,
        contributor: admin,
      })
      .rpc();

    // 2. Add more participants
    const participant4 = Keypair.generate();
    const airdropSig = await provider.connection.requestAirdrop(
      participant4.publicKey,
      LAMPORTS_PER_SOL
    );
    await provider.connection.confirmTransaction(airdropSig);

    const [participant4Pda] = PublicKey.findProgramAddressSync(
      [Buffer.from("participant"), participant4.publicKey.toBuffer()],
      program.programId
    );

    await program.methods
      .updateParticipant(new BN("100000000000000")) // 1M BALL tokens
      .accounts({
        participant: participant4Pda,
        lotteryState: lotteryStatePda,
        user: participant4.publicKey,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([participant4])
      .rpc();

    // 3. Create a new lottery
    const [flowLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("hourly"),
        new BN(5).toArrayLike(Buffer, "le", 4),
      ],
      program.programId
    );

    const pastTime = new BN(Math.floor(Date.now() / 1000) - 1);
    
    await program.methods
      .createLottery({ hourly: {} }, pastTime)
      .accounts({
        lottery: flowLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // 4. Execute lottery
    await program.methods
      .executeLottery(
        { hourly: {} },
        5,
        participant4.publicKey, // participant4 wins
        new BN(77777),
        "flow_test_execution"
      )
      .accounts({
        lottery: flowLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // 5. Pay winner
    const winnerBalanceBefore = await provider.connection.getBalance(participant4.publicKey);
    
    await program.methods
      .payWinner({ hourly: {} }, 5)
      .accounts({
        lottery: flowLotteryPda,
        lotteryState: lotteryStatePda,
        winner: participant4.publicKey,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    const winnerBalanceAfter = await provider.connection.getBalance(participant4.publicKey);
    const lottery = await program.account.lottery.fetch(flowLotteryPda);

    console.log("🎯 Complete Flow Results:", {
      winner: lottery.winner.toString(),
      jackpotAmount: lottery.jackpotAmount.toString(),
      balanceIncrease: winnerBalanceAfter - winnerBalanceBefore,
      status: lottery.status,
    });

    // Assertions
    assert(lottery.winner.equals(participant4.publicKey));
    assert(JSON.stringify(lottery.status) === JSON.stringify({ completed: {} }));
    assert(winnerBalanceAfter > winnerBalanceBefore);
    assert(lottery.jackpotAmount.gt(new BN(0)));

    console.log("✅ Complete lottery flow test passed!");
  });

  // Performance and stress tests
  it("Should handle multiple rapid participant updates", async () => {
    console.log("⚡ Testing rapid participant updates...");

    const updates = [
      new BN("10000000000000"), // 100K BALL
      new BN("20000000000000"), // 200K BALL
      new BN("30000000000000"), // 300K BALL
      new BN("15000000000000"), // 150K BALL
      new BN("25000000000000"), // 250K BALL
    ];

    for (let i = 0; i < updates.length; i++) {
      await program.methods
        .updateParticipant(updates[i])
        .accounts({
          participant: participant1Pda,
          lotteryState: lotteryStatePda,
          user: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
    }

    const finalParticipant = await program.account.participant.fetch(participant1Pda);
    const finalLotteryState = await program.account.lotteryState.fetch(lotteryStatePda);

    console.log("⚡ Rapid Updates Result:", {
      finalBalance: finalParticipant.ballBalance.toString(),
      finalTickets: finalParticipant.ticketsCount.toString(),
      totalTickets: finalLotteryState.totalTickets.toString(),
    });

    // Should have the last update value
    assert(finalParticipant.ballBalance.eq(updates[updates.length - 1]));
    
    console.log("✅ Rapid updates test passed!");
  });

  it("Should validate lottery state consistency", async () => {
    console.log("🔍 Validating lottery state consistency...");

    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
    
    // Fetch all participants to validate totals
    const allParticipants = await program.account.participant.all();
    const eligibleParticipants = allParticipants.filter(p => p.account.isEligible);
    const totalTicketsFromParticipants = allParticipants.reduce(
      (sum, p) => sum.add(p.account.ticketsCount),
      new BN(0)
    );

    console.log("🔍 Consistency Check:", {
      stateParticipants: lotteryState.totalParticipants.toString(),
      eligibleParticipants: eligibleParticipants.length,
      stateTickets: lotteryState.totalTickets.toString(),
      calculatedTickets: totalTicketsFromParticipants.toString(),
      allParticipantsCount: allParticipants.length,
    });

    // Validate consistency
    assert(lotteryState.totalParticipants.eq(new BN(eligibleParticipants.length)));
    assert(lotteryState.totalTickets.eq(totalTicketsFromParticipants));

    console.log("✅ State consistency validated!");
  });

  it("Should test edge case: lottery with no participants", async () => {
    console.log("🚫 Testing lottery with no participants...");

    // Remove all participants by setting their balance to 0
    const allParticipants = await program.account.participant.all();
    
    for (const participantAccount of allParticipants) {
      if (participantAccount.account.ticketsCount.gt(new BN(0))) {
        // Find the signer for this participant
        let signer;
        if (participantAccount.account.wallet.equals(admin)) {
          signer = provider.wallet;
        } else if (participantAccount.account.wallet.equals(participant2.publicKey)) {
          signer = participant2;
        } else {
          // Skip unknown participants for this test
          continue;
        }

        try {
          await program.methods
            .updateParticipant(new BN(0))
            .accounts({
              participant: participantAccount.publicKey,
              lotteryState: lotteryStatePda,
              user: participantAccount.account.wallet,
              systemProgram: anchor.web3.SystemProgram.programId,
            })
            .signers(signer === provider.wallet ? [] : [signer])
            .rpc();
        } catch (error) {
          console.log("Skipping participant update:", error.message);
        }
      }
    }

    // Create lottery with no participants
    const [noParticipantsLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("hourly"),
        new BN(6).toArrayLike(Buffer, "le", 4),
      ],
      program.programId
    );

    const pastTime = new BN(Math.floor(Date.now() / 1000) - 1);
    
    await program.methods
      .createLottery({ hourly: {} }, pastTime)
      .accounts({
        lottery: noParticipantsLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // Try to execute lottery with no participants (should fail)
    try {
      await program.methods
        .executeLottery(
          { hourly: {} },
          6,
          admin,
          new BN(88888),
          "no_participants_test"
        )
        .accounts({
          lottery: noParticipantsLotteryPda,
          lotteryState: lotteryStatePda,
          admin: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      
      assert.fail("Should have failed with NoParticipants error");
    } catch (error) {
      console.log("✅ Correctly prevented execution with no participants");
      assert(error.message.includes("NoParticipants") || error.message.includes("6001"));
    }
  });

  it("Should test lottery with zero jackpot", async () => {
    console.log("💰 Testing lottery with zero jackpot...");

    // The hourly jackpot should be 0 after previous executions
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
    
    if (lotteryState.hourlyJackpotSol.gt(new BN(0))) {
      console.log("Hourly jackpot not zero, skipping this test");
      return;
    }

    // Add a participant back
    await program.methods
      .updateParticipant(new BN("50000000000000"))
      .accounts({
        participant: participant1Pda,
        lotteryState: lotteryStatePda,
        user: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // Create lottery with zero jackpot
    const [zeroJackpotLotteryPda] = PublicKey.findProgramAddressSync(
      [
        Buffer.from("lottery"),
        Buffer.from("hourly"),
        new BN(7).toArrayLike(Buffer, "le", 4),
      ],
      program.programId
    );

    const pastTime = new BN(Math.floor(Date.now() / 1000) - 1);
    
    await program.methods
      .createLottery({ hourly: {} }, pastTime)
      .accounts({
        lottery: zeroJackpotLotteryPda,
        lotteryState: lotteryStatePda,
        admin: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    // Try to execute lottery with zero jackpot (should fail)
    try {
      await program.methods
        .executeLottery(
          { hourly: {} },
          7,
          admin,
          new BN(99999),
          "zero_jackpot_test"
        )
        .accounts({
          lottery: zeroJackpotLotteryPda,
          lotteryState: lotteryStatePda,
          admin: admin,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      
      assert.fail("Should have failed with InsufficientJackpot error");
    } catch (error) {
      console.log("✅ Correctly prevented execution with zero jackpot");
      assert(error.message.includes("InsufficientJackpot") || error.message.includes("6004"));
    }
  });

  it("Should test maximum values and boundaries", async () => {
    console.log("🔢 Testing boundary values...");

    // Test with very large BALL balance
    const largeBalance = new BN("1000000000000000000"); // 10B BALL tokens
    const expectedLargeTickets = largeBalance.div(new BN("10000000000")); // 1M tickets

    await program.methods
      .updateParticipant(largeBalance)
      .accounts({
        participant: participant1Pda,
        lotteryState: lotteryStatePda,
        user: admin,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .rpc();

    const participant = await program.account.participant.fetch(participant1Pda);
    
    console.log("🔢 Large Balance Test:", {
      balance: participant.ballBalance.toString(),
      tickets: participant.ticketsCount.toString(),
      expectedTickets: expectedLargeTickets.toString(),
    });

    assert(participant.ballBalance.eq(largeBalance));
    assert(participant.ticketsCount.eq(expectedLargeTickets));

    // Test with minimum ticket requirement boundary
    const minRequirement = new BN(100);
    
    await program.methods
      .updateConfig(minRequirement)
      .accounts({
        lotteryState: lotteryStatePda,
        admin: admin,
      })
      .rpc();

    // Test participant with exactly minimum tickets
    const exactMinBalance = minRequirement.mul(new BN("10000000000")); // 100 * 10,000 BALL
    
    await program.methods
      .updateParticipant(exactMinBalance)
      .accounts({
        participant: participant2Pda,
        lotteryState: lotteryStatePda,
        user: participant2.publicKey,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([participant2])
      .rpc();

    const minParticipant = await program.account.participant.fetch(participant2Pda);
    
    console.log("🔢 Minimum Boundary Test:", {
      balance: minParticipant.ballBalance.toString(),
      tickets: minParticipant.ticketsCount.toString(),
      isEligible: minParticipant.isEligible,
      minRequirement: minRequirement.toString(),
    });

    assert(minParticipant.ticketsCount.eq(minRequirement));
    assert(minParticipant.isEligible === true);

    // Test just below minimum
    const belowMinBalance = minRequirement.sub(new BN(1)).mul(new BN("10000000000"));
    
    const participant3 = Keypair.generate();
    const airdropSig = await provider.connection.requestAirdrop(
      participant3.publicKey,
      LAMPORTS_PER_SOL
    );
    await provider.connection.confirmTransaction(airdropSig);

    const [participant3Pda] = PublicKey.findProgramAddressSync(
      [Buffer.from("participant"), participant3.publicKey.toBuffer()],
      program.programId
    );

    await program.methods
      .updateParticipant(belowMinBalance)
      .accounts({
        participant: participant3Pda,
        lotteryState: lotteryStatePda,
        user: participant3.publicKey,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([participant3])
      .rpc();

    const belowMinParticipant = await program.account.participant.fetch(participant3Pda);
    
    console.log("🔢 Below Minimum Test:", {
      tickets: belowMinParticipant.ticketsCount.toString(),
      isEligible: belowMinParticipant.isEligible,
    });

    assert(belowMinParticipant.ticketsCount.eq(minRequirement.sub(new BN(1))));
    assert(belowMinParticipant.isEligible === false);

    console.log("✅ Boundary value tests passed!");
  });

  it("Should test string handling for transaction signatures", async () => {
    console.log("📝 Testing string handling...");

    // Test with various string lengths
    const shortSig = "short";
    const normalSig = "normal_transaction_signature_123456789";
    const longSig = "very_long_transaction_signature_with_lots_of_characters_to_test_string_handling_in_solana_program_" + "x".repeat(100);

    // Test contribution with different signature lengths
    await program.methods
      .contributeToJackpot(new BN(100000), shortSig)
      .accounts({
        lotteryState: lotteryStatePda,
        contributor: admin,
      })
      .rpc();

    await program.methods
      .contributeToJackpot(new BN(100000), normalSig)
      .accounts({
        lotteryState: lotteryStatePda,
        contributor: admin,
      })
      .rpc();

    // Long signature might fail due to transaction size limits, but let's try
    try {
      await program.methods
        .contributeToJackpot(new BN(100000), longSig)
        .accounts({
          lotteryState: lotteryStatePda,
          contributor: admin,
        })
        .rpc();
      console.log("✅ Long signature handled successfully");
    } catch (error) {
      console.log("⚠️ Long signature failed (expected due to size limits)");
    }

    console.log("✅ String handling tests completed!");
  });

  it("Should test final comprehensive state", async () => {
    console.log("🏁 Final comprehensive state test...");

    // Get all accounts
    const lotteryState = await program.account.lotteryState.fetch(lotteryStatePda);
    const allParticipants = await program.account.participant.all();
    const allLotteries = await program.account.lottery.all();

    console.log("🏁 FINAL COMPREHENSIVE STATE:");
    console.log("=" .repeat(50));
    
    console.log("📊 Lottery State:");
    console.log(`  Admin: ${lotteryState.admin.toString()}`);
    console.log(`  BALL Token: ${lotteryState.ballTokenMint.toString()}`);
    console.log(`  Hourly Jackpot: ${lotteryState.hourlyJackpotSol.toString()} lamports`);
    console.log(`  Daily Jackpot: ${lotteryState.dailyJackpotSol.toString()} lamports`);
    console.log(`  Total Participants: ${lotteryState.totalParticipants.toString()}`);
    console.log(`  Total Tickets: ${lotteryState.totalTickets.toString()}`);
    console.log(`  Hourly Draws: ${lotteryState.hourlyDrawCount}`);
    console.log(`  Daily Draws: ${lotteryState.dailyDrawCount}`);
    console.log(`  Min Ticket Requirement: ${lotteryState.minTicketRequirement.toString()}`);
    console.log(`  Is Paused: ${lotteryState.isPaused}`);
    console.log(`  Last Hourly Draw: ${lotteryState.lastHourlyDraw.toString()}`);
    console.log(`  Last Daily Draw: ${lotteryState.lastDailyDraw.toString()}`);

    console.log("\n👥 All Participants:");
    allParticipants.forEach((p, index) => {
      console.log(`  ${index + 1}. ${p.account.wallet.toString()}`);
      console.log(`     Balance: ${p.account.ballBalance.toString()} BALL`);
      console.log(`     Tickets: ${p.account.ticketsCount.toString()}`);
      console.log(`     Eligible: ${p.account.isEligible}`);
      console.log(`     Last Updated: ${p.account.lastUpdated.toString()}`);
    });

    console.log("\n🎰 All Lotteries:");
    allLotteries.forEach((l, index) => {
      const lotteryType = l.account.lotteryType.hasOwnProperty('hourly') ? 'Hourly' : 'Daily';
      const status = Object.keys(l.account.status)[0];
      
      
      console.log(`  ${index + 1}. ${lotteryType} Lottery #${l.account.drawId}`);
      console.log(`     Status: ${status}`);
      console.log(`     Scheduled: ${l.account.scheduledTime.toString()}`);
      console.log(`     Executed: ${l.account.executedTime.toString()}`);
      console.log(`     Jackpot: ${l.account.jackpotAmount.toString()} lamports`);
      console.log(`     Participants: ${l.account.totalParticipants.toString()}`);
      console.log(`     Tickets: ${l.account.totalTickets.toString()}`);
      console.log(`     Winner: ${l.account.winner.toString()}`);
      console.log(`     VRF Seed: ${l.account.vrfSeed.toString()}`);
      console.log(`     Payout Time: ${l.account.payoutTime.toString()}`);
      console.log(`     Transaction: ${l.account.transactionSignature}`);
    });

    // Final validations
    console.log("\n✅ Final Validations:");
    
    // Validate participant count consistency
    const eligibleCount = allParticipants.filter(p => p.account.isEligible).length;
    console.log(`  Eligible participants match: ${lotteryState.totalParticipants.eq(new BN(eligibleCount))}`);
    
    // Validate ticket count consistency
    const totalTickets = allParticipants.reduce((sum, p) => sum.add(p.account.ticketsCount), new BN(0));
    console.log(`  Total tickets match: ${lotteryState.totalTickets.eq(totalTickets)}`);
    
    // Validate lottery counts
    const hourlyLotteries = allLotteries.filter(l => l.account.lotteryType.hasOwnProperty('hourly'));
    const dailyLotteries = allLotteries.filter(l => l.account.lotteryType.hasOwnProperty('daily'));
    console.log(`  Hourly lottery count: ${hourlyLotteries.length} (state: ${lotteryState.hourlyDrawCount})`);
    console.log(`  Daily lottery count: ${dailyLotteries.length} (state: ${lotteryState.dailyDrawCount})`);
    
    // Validate completed lotteries
    const completedLotteries = allLotteries.filter(l => 
      l.account.status.hasOwnProperty('completed')
    );
    console.log(`  Completed lotteries: ${completedLotteries.length}`);
    
    // Validate all completed lotteries have winners and payouts
    const validCompletedLotteries = completedLotteries.filter(l => 
      !l.account.winner.equals(PublicKey.default) && 
      l.account.payoutTime.gt(new BN(0))
    );
    console.log(`  Valid completed lotteries: ${validCompletedLotteries.length}`);

    // Final assertions
    assert(lotteryState.totalParticipants.eq(new BN(eligibleCount)));
    assert(lotteryState.totalTickets.eq(totalTickets));
    assert(lotteryState.hourlyDrawCount >= hourlyLotteries.length);
    assert(lotteryState.dailyDrawCount >= dailyLotteries.length);
    assert(completedLotteries.length === validCompletedLotteries.length);

    console.log("\n🎉 ALL TESTS COMPLETED SUCCESSFULLY!");
    console.log("=" .repeat(50));
  });

  // Cleanup test
  after(async () => {
    console.log("\n🧹 Cleanup phase...");
    
    try {
      // Get final balances
      const adminBalance = await provider.connection.getBalance(admin);
      const programBalance = await provider.connection.getBalance(lotteryStatePda);
      
      console.log("💰 Final Balances:");
      console.log(`  Admin: ${adminBalance / LAMPORTS_PER_SOL} SOL`);
      console.log(`  Program: ${programBalance / LAMPORTS_PER_SOL} SOL`);
      
      // Log total accounts created
      const allParticipants = await program.account.participant.all();
      const allLotteries = await program.account.lottery.all();
      
      console.log("📊 Total Accounts Created:");
      console.log(`  Participants: ${allParticipants.length}`);
      console.log(`  Lotteries: ${allLotteries.length}`);
      console.log(`  Total: ${allParticipants.length + allLotteries.length + 1} (including lottery state)`);
      
    } catch (error) {
      console.log("⚠️ Cleanup error:", error.message);
    }
    
    console.log("✅ Cleanup completed!");
  });
});
