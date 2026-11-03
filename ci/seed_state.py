#!/usr/bin/env python3
"""Ilk kurulum / elle guncelleme: yerel dosyalardan state.enc uretir.

  python3 ci/seed_state.py --positions P.json --notes N.json --data D.js --out state.enc [--keyfile K]
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcrypt

ap = argparse.ArgumentParser()
ap.add_argument("--positions", required=True)
ap.add_argument("--notes", required=True)
ap.add_argument("--data", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--keyfile")
a = ap.parse_args()
key = pcrypt.load_key(a.keyfile)
state = {"v": 1,
         "positions": json.load(open(a.positions, encoding="utf-8")),
         "notes": json.load(open(a.notes, encoding="utf-8")),
         "prev_js": open(a.data, encoding="utf-8").read()}
open(a.out, "w", encoding="utf-8").write(
    pcrypt.encrypt(json.dumps(state, ensure_ascii=False).encode("utf-8"), key))
print("state.enc yazildi:", os.path.getsize(a.out), "bayt")
