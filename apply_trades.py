#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bekleyen alim/satim kuyrugunu positions.json'a uygular.

Kullanim:
    python3 apply_trades.py positions.json trades.json --out positions.json --results results.json

Girdi : positions.json (mevcut pozisyonlar)
        trades.json     ({"trades": [{"id","symbol","side":"buy"|"sell","shares","price"}, ...]})
Cikti : positions.json (guncellenmis, --out ile ayni ya da farkli dosyaya)
        results.json    ({"applied": [...], "errors": [...]})

Kurallar:
- Yalnizca positions.json'da ZATEN VAR olan semboller islenir. Yeni sembol eklemek bu
  kuyruktan desteklenmiyor (elle yapilmali, DEVAM-PROMPTU.md'deki adimlar gecerli).
- buy: adet artar, ortalama maliyet agirlikli ortalama ile yeniden hesaplanir.
- sell: adet azalir, ortalama maliyet DEGISMEZ (kalan hisselerin maliyeti ayni kalir).
  Satilan adet mevcuttan fazlaysa islem reddedilir (error).
- Adet 0'a (1e-4 tolerans) inerse pozisyon TAMAMEN kaldirilir.
- avg_cost_is_estimate bayragi bu script tarafindan DEGISTIRILMEZ -- eski pay hala
  tahmine dayali oldugu icin "artik gercek" denemez; bu ayri bir P0 is (gercek maliyetleri
  toplu girmek).
- Yalniz Python standart kutuphanesi kullanir.
"""

import argparse, json, sys

EPS = 1e-4


def apply_one(cfg_positions, trade):
    sym = trade.get("symbol")
    side = trade.get("side")
    shares = trade.get("shares")
    price = trade.get("price")

    if not sym or side not in ("buy", "sell"):
        return None, "gecersiz islem (symbol/side eksik ya da yanlis)"
    try:
        shares = float(shares)
        price = float(price)
    except (TypeError, ValueError):
        return None, "gecersiz adet/fiyat"
    if shares <= 0 or price <= 0:
        return None, "adet ve fiyat pozitif olmali"

    idx = next((i for i, p in enumerate(cfg_positions) if p["symbol"] == sym), None)
    if idx is None:
        return None, "sembol portfoyde yok (yeni sembol ekleme bu kuyruktan desteklenmiyor)"

    pos = cfg_positions[idx]
    old_shares = float(pos["shares"])
    old_avg = float(pos["avg_cost"])

    if side == "buy":
        new_shares = old_shares + shares
        new_avg = (old_shares * old_avg + shares * price) / new_shares
        pos["shares"] = round(new_shares, 6)
        pos["avg_cost"] = round(new_avg, 4)
        removed = False
    else:  # sell
        if shares > old_shares + EPS:
            return None, "yetersiz adet (portfoyde %.4g var, %.4g satilmak isteniyor)" % (old_shares, shares)
        new_shares = old_shares - shares
        removed = new_shares <= EPS
        if removed:
            cfg_positions.pop(idx)
        else:
            pos["shares"] = round(new_shares, 6)
            # avg_cost degismez -- satis kalan hisselerin maliyet tabanini degistirmez.

    return {"symbol": sym, "side": side, "shares": shares, "price": price,
            "removed": removed,
            "new_shares": 0.0 if removed else pos["shares"],
            "new_avg_cost": old_avg if removed else pos["avg_cost"]}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("positions")
    ap.add_argument("trades")
    ap.add_argument("--out", default=None, help="Varsayilan: positions.json'un ustune yazar.")
    ap.add_argument("--results", default="results.json")
    a = ap.parse_args()
    out_path = a.out or a.positions

    cfg = json.load(open(a.positions, encoding="utf-8"))
    trades_doc = json.load(open(a.trades, encoding="utf-8"))
    trades = trades_doc.get("trades") or []

    applied, errors = [], []
    for t in trades:
        result, err = apply_one(cfg["positions"], t)
        if err:
            errors.append({"id": t.get("id"), "symbol": t.get("symbol"), "reason": err})
        else:
            result["id"] = t.get("id")
            applied.append(result)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(a.results, "w", encoding="utf-8") as f:
        json.dump({"applied": applied, "errors": errors}, f, ensure_ascii=False, indent=1)

    print("ISLEM SONUCU: %d uygulandi, %d hata" % (len(applied), len(errors)))
    for r in applied:
        tag = "KAPANDI" if r["removed"] else ("%.4g adet, ort. maliyet $%.4g" % (r["new_shares"], r["new_avg_cost"]))
        print("  + %s %s %.4g@$%.4g -> %s" % (r["symbol"], r["side"], r["shares"], r["price"], tag))
    for e in errors:
        print("  ! %s (%s): %s" % (e.get("symbol"), e.get("id"), e["reason"]))


if __name__ == "__main__":
    main()
