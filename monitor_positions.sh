#!/bin/bash
# ============================================================================
# POSITION MONITOR — Watch when all 3 bots close their positions
# ============================================================================
# Run this while waiting for positions to close
# Shows real-time status of Sniper1, Grogu1, Boba1
# ============================================================================

watch -n 5 'echo "=== LIVE POSITION STATUS ===" && echo "Time: $(date)" && echo "" && \
sshpass -p "B@nd73610421" ssh -o StrictHostKeyChecking=no root@187.127.114.34 << "SSHEOF" 2>/dev/null || echo "SSH error"

echo "--- SNIPER1 (ETH signal) ---"
docker logs opt-app-paper-1 --tail 5 2>/dev/null | grep -E "OPENED|CLOSED|position" | tail -3

echo ""
echo "--- GROGU1 (ETH straddle) ---"
docker logs opt-app-eth_straddle_paper-1 --tail 10 2>/dev/null | grep -E "OPENED|CLOSED" | tail -3

echo ""
echo "--- BOBA1 (BTC straddle) ---"
docker logs opt-app-btc_paper-1 --tail 10 2>/dev/null | grep -E "OPENED|CLOSED" | tail -3

echo ""
echo "🟢 All closed → Run: bash ~/Desktop/options/grogu_instant_deploy.sh"

SSHEOF
'
