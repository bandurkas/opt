#!/usr/bin/env bash
# One-shot paper-bot health + progress report toward the go-live gate.
set -e
PSQL=(docker exec opt-app-postgres-1 psql -U user -d options_assistant -tA -F'|')
echo "===== PAPER BOT STATUS  $(date -u '+%Y-%m-%d %H:%M UTC') ====="
echo "-- loop alive? last 3 log lines --"
docker logs --tail 3 opt-app-paper-1 2>&1 | sed 's/^/   /'
echo "-- recent errors (last 200 log lines) --"
ERRS=$(docker logs --tail 200 opt-app-paper-1 2>&1 | grep -c "\[paper\] error" || true)
echo "   error lines in last 200: ${ERRS}"
echo "-- closed cycles --"
"${PSQL[@]}" -c "SELECT 'closed='||count(*)||'  realized=\$'||round(sum(pnl_usd)::numeric,2)||'  avg='||round(avg(pnl_pct)::numeric,2)||'%  wr='||round(100.0*sum((pnl_usd>0)::int)/NULLIF(count(*),0),1)||'%' FROM paper_positions WHERE status ~ '^closed';"
echo "-- by exit reason --"
"${PSQL[@]}" -c "SELECT '   '||exit_reason||': n='||count(*)||' sum=\$'||round(sum(pnl_usd)::numeric,2) FROM paper_positions WHERE status ~ '^closed' GROUP BY exit_reason ORDER BY exit_reason;"
echo "-- equity / risk state --"
"${PSQL[@]}" -c "SELECT 'equity=\$'||round((start_equity_usd+(SELECT coalesce(sum(pnl_usd),0) FROM paper_positions WHERE status ~ '^closed'))::numeric,2)||'  consec_losses='||consec_losses||'  cb_active='||(cb_cooldown_until_ms>extract(epoch from now())*1000)::text||'  recent_pnls(n)='||jsonb_array_length(recent_pnls_json) FROM paper_state;"
echo "-- open now --"
"${PSQL[@]}" -c "SELECT 'open='||count(*)||'  strikes='||coalesce(string_agg(DISTINCT side||strike::int::text,','),'-') FROM paper_positions WHERE status IN ('open','half_closed_tp1');"
echo "-- GATE: need >=20-30 closed cycles, SL+CB+dynsize observed, avg within 30-50% of backtest --"
