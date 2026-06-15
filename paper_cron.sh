#!/usr/bin/env bash
# Scheduled paper-bot monitor: detects noteworthy events, Telegrams on them.
# Run by cron every few hours. Server-side, uses the bot's existing TG creds.
set -euo pipefail
APP=/root/opt-app
STATE=$APP/.paper_monitor_state
LOG=$APP/paper_monitor.log
set -a; . $APP/.env 2>/dev/null || true; set +a

Q="docker exec opt-app-postgres-1 psql -U user -d options_assistant -tA -F| -c"
read -r CLOSED N_SL N_TP2 N_TIME REALIZED AVG WR EQUITY CONSEC CBA DYN <<<"$(
  $Q "
  SELECT
    (SELECT count(*) FROM paper_positions WHERE status ~ '^closed'),
    (SELECT count(*) FROM paper_positions WHERE status='closed_sl'),
    (SELECT count(*) FROM paper_positions WHERE status='closed_tp2'),
    (SELECT count(*) FROM paper_positions WHERE status='closed_time'),
    (SELECT round(coalesce(sum(pnl_usd),0)::numeric,2) FROM paper_positions WHERE status ~ '^closed'),
    (SELECT round(coalesce(avg(pnl_pct),0)::numeric,2) FROM paper_positions WHERE status ~ '^closed'),
    (SELECT round(100.0*sum((pnl_usd>0)::int)/NULLIF(count(*),0),1) FROM paper_positions WHERE status ~ '^closed'),
    (SELECT round((start_equity_usd+(SELECT coalesce(sum(pnl_usd),0) FROM paper_positions WHERE status ~ '^closed'))::numeric,2) FROM paper_state),
    (SELECT consec_losses FROM paper_state),
    (SELECT (cb_cooldown_until_ms>extract(epoch from now())*1000)::int FROM paper_state),
    (SELECT (jsonb_array_length(recent_pnls_json)>=10 AND
      (SELECT count(*) FROM jsonb_array_elements(recent_pnls_json) WITH ORDINALITY e(v,i)
       WHERE i>jsonb_array_length(recent_pnls_json)-10 AND (v::numeric)>0)<4)::int FROM paper_state)
  " | tr '|' ' '
)"

TS=$(date -u '+%Y-%m-%d %H:%M UTC')
echo "$TS closed=$CLOSED sl=$N_SL tp2=$N_TP2 time=$N_TIME realized=$REALIZED avg=$AVG wr=$WR eq=$EQUITY consec=$CONSEC cb=$CBA dyn=$DYN" >>"$LOG"

# previous snapshot
P_CLOSED=0; P_SL=0; P_CB=0; P_DYN=0; P_MILE=0; P_GATE=0
[ -f "$STATE" ] && . "$STATE"

ALERT=""
[ "$N_SL" -gt "$P_SL" ] && ALERT+="🔴 НОВЫЙ SL (всего sl=$N_SL) — проверить риск-контроль%0A"
[ "$CBA" = 1 ] && [ "$P_CB" != 1 ] && ALERT+="⏸ CB АКТИВИРОВАН (5 убытков подряд → пауза 48ч)%0A"
[ "$DYN" = 1 ] && [ "$P_DYN" != 1 ] && ALERT+="📉 DYNSIZE включился (WR10<40%% → размер ×0.5)%0A"
MILE=$(( CLOSED/5 ))
[ "$MILE" -gt "$P_MILE" ] && ALERT+="📈 Циклов: $CLOSED (tp2=$N_TP2 sl=$N_SL time=$N_TIME) avg=$AVG%% WR=$WR%% eq=\$$EQUITY%0A"
GATE=0; [ "$CLOSED" -ge 20 ] && GATE=1
[ "$GATE" = 1 ] && [ "$P_GATE" != 1 ] && ALERT+="✅ ГЕЙТ: ≥20 циклов закрыто — пора сверять paper↔бэктест и решать по live%0A"

if [ -n "$ALERT" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  MSG="🤖 Paper-бот ($TS)%0A${ALERT}"
  curl -s --max-time 15 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data "text=${MSG}" >/dev/null || true
fi

cat >"$STATE" <<EOF
P_CLOSED=$CLOSED
P_SL=$N_SL
P_CB=$CBA
P_DYN=$DYN
P_MILE=$MILE
P_GATE=$GATE
EOF
