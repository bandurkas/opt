"""Hourly Telegram delivery to existing authorized Artur bot chat."""
import json,os,sqlite3,time,urllib.request,sys
from pathlib import Path
from reporting import summary
ROOT=Path(__file__).resolve().parent

def main():
 import fcntl
 with (ROOT/'telegram-report.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  slot=int(time.time())//3600
  state=ROOT/'telegram-report-state.json'
  if state.exists() and json.loads(state.read_text()).get('slot')==slot:
   print('already_sent');return
  db=sqlite3.connect(f'file:{ROOT / "paper.sqlite"}?mode=ro',uri=True)
  s=json.loads(db.execute('select body from state where id=1').fetchone()[0]);text=summary(db,s);db.close()
  if '--preview' in sys.argv:print(text);return
  token=os.environ['TELEGRAM_BOT_TOKEN'];chat=os.environ['TELEGRAM_CHAT_ID']
  req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=json.dumps({'chat_id':chat,'text':text,'disable_web_page_preview':True}).encode(),headers={'Content-Type':'application/json'},method='POST')
  try:
   with urllib.request.urlopen(req,timeout=20) as r:result=json.load(r)
   if not result.get('ok'):raise RuntimeError('Telegram rejected message')
  except Exception:
   print('Telegram delivery failed or ambiguous; not retried automatically',file=sys.stderr);sys.exit(1)
  tmp=state.with_suffix('.tmp');tmp.write_text(json.dumps({'slot':slot,'message_id':result['result']['message_id']}));tmp.replace(state)
  print('sent message_id='+str(result['result']['message_id']))
if __name__=='__main__':main()
