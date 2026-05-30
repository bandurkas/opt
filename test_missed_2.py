import sys
sys.path.insert(0, '/app')
from services.missed_signals import compute_missed_signals

res = compute_missed_signals(14, True)
print(f"Skipped by CB: {res.get('n_skipped_by_cb')}")
print(f"Skipped by busy: {res.get('n_skipped_by_busy')}")
print(f"Skipped by margin: {res.get('n_skipped_by_margin')}")
