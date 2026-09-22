#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bekleyen alim/satim kuyrugunu positions.json'a uygular.

Kullanim:
    python3 apply_trades.py positions.json trades.json --out positions.json --results results.json [--quotes quotes.json]

Girdi : positions.json (mevcut pozisyonlar)
        trades.json     ({"trades": [{"id","symbol","side":"buy"|"sell","shares","price",
                         "note","submitted_at"}, ...]}) -- note/submitted_at opsiyonel ama
                         KARAR GUNLUGU icin onemli, rutin promptu bunlari da kopyalar.
        --quotes        quotes.json (opsiyonel). Verilirse yeni "lots" kaydinin price_eur'u
                         alim gunundeki EURUSD kapanisiyla hesaplanir (tax_de.fx_on). Verilmezse
                         price_eur None birakilir -- build.py build_tax zaten pe None oldugunda
                         ayni fx_on hesabini kendisi yapiyor, tek fark bu script'in de ayni
                         degeri positions.json'a KALICI yazmasi (denetim/gorunurluk icin).
Cikti : positions.json (guncellenmis, --out ile ayni ya da farkli dosyaya; basarili her islem
                        "decisions" dizisine de eklenir -- karar gunlugu)
        results.json    ({"applied": [...], "errors": [...], "skipped": [...],
                         "decisions_added": N})

Kurallar:
- Yalnizca positions.json'da ZATEN VAR olan semboller islenir. Yeni sembol eklemek bu
  kuyruktan desteklenmiyor (elle yapilmali, DEVAM-PROMPTU.md'deki adimlar gecerli).
- buy: adet artar, ortalama maliyet agirlikli ortalama ile yeniden hesaplanir. "lots" varsa
  (yoksa olusturulmaz -- eski pozisyonlarda tam alim gecmisi yok) yeni bir lot
  {date, shares, price_usd, price_eur} olarak eklenir.
- sell: adet azalir, ortalama maliyet DEGISMEZ (kalan hisselerin maliyeti ayni kalir).
  Satilan adet mevcuttan fazlaysa islem reddedilir (error). "lots" varsa FIFO (en eski alim
  once) ile tuketilir, tax_de.fifo_consume ile ayni mantik -- build.py'nin build_tax'ta
  yaptigi FIFO ile TUTARLI kalmasi icin.
- Adet 0'a (1e-4 tolerans) inerse pozisyon TAMAMEN kaldirilir.
- avg_cost_is_estimate bayragi bu script tarafindan DEGISTIRILMEZ -- eski pay hala
  tahmine dayali oldugu icin "artik gercek" denemez; bu ayri bir P0 is (gercek maliyetleri
  toplu girmek).
- LOTS TUTARLILIGI: build.py build_tax, sum(lots.shares) pos.shares'e esit degilse lots'u
  TAMAMEN yok sayip tahmine duser (yanlis EUR rakami gostermemek icin). Bu yuzden her
  buy/sell'de lots'un toplami pos.shares ile birebir esitlenir; lots hic yoksa (eski
  pozisyon) dokunulmaz, olusturulmaz -- kismi/varsayimsal lot uydurmak yanlis vergi
  rakamina yol acar.
- KARAR GUNLUGU: basarili her islem, positions.json'daki "decisions" dizisine
  {id, date, symbol, side, shares, price, note} olarak EKLENIR (mevcut kayitlar silinmez,
  yalnizca son 200 tutulur). Sayfa bu listeyi "Karar Gunlugu" bolumunde, o gunden bugune
  fiyat degisimiyle birlikte gosterir. Hatali islem gunluge YAZILMAZ (uygulanmadi zaten).
- ISLENEN ID'LER: positions.json'un "processed_trade_ids" listesinde daha once gorulen
  bir id tekrar GELIRSE sessizce ATLANIR (basarili da hatali da sayilmaz) -- boylece rutin
  ArtifactData'ya YAZMADAN (bulut rutininde izin istemi cikarip askida kalan islem) ayni
  islemi iki kez uygulamaz. Liste en fazla son 300 id'yi tutar (eskiler dusurulur).
- Yalniz Python standart kutuphanesi kullanir.
"""

import argparse, json, sys
from datetime import date, datetime, timezone

try:
    import tax_de
except ImportError:
    tax_de = None

EPS = 1e-4
MAX_PROCESSED_IDS = 300
MAX_DECISIONS = 200


def trade_date_str(trade):
    """Islemin karar/lot tarihi: submitted_at (girildigi an) varsa o, yoksa bugun (UTC)."""
    submitted = str(trade.get("submitted_at") or "")
    if len(submitted) >= 10:
        return submitted[:10]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def price_eur_on(day_str, price_usd, fx):
    """day_str (YYYY-MM-DD) icin USD fiyati EUR'a cevirir (tax_de.fx_on, alim gunu kuru).

    fx ya da tax_de yoksa None doner -- build.py build_tax pe None oldugunda ayni hesabi
    kendisi yapiyor, lot'un GEREKSIZ yere tahmini/yanlis bir deger tasimasindansa bos kalmasi
    daha guvenli.
    """
    if not fx or tax_de is None:
        return None
    try:
        rate, _ = tax_de.fx_on(date.fromisoformat(day_str), fx)
    except (ValueError, TypeError):
        return None
    if not rate:
        return None
    return round(price_usd / rate, 6)


def apply_one(cfg_positions, trade, fx=None):
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
    day_str = trade_date_str(trade)

    if side == "buy":
        new_shares = old_shares + shares
        new_avg = (old_shares * old_avg + shares * price) / new_shares
        pos["shares"] = round(new_shares, 6)
        pos["avg_cost"] = round(new_avg, 4)
        removed = False
        if pos.get("lots"):
            pos["lots"].append({
                "date": day_str, "shares": round(shares, 6),
                "price_usd": round(price, 6),
                "price_eur": price_eur_on(day_str, price, fx),
            })
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
        if not removed and pos.get("lots"):
            if tax_de is not None:
                _, rest = tax_de.fifo_consume(pos["lots"], shares)
                pos["lots"] = rest
            else:
                # tax_de yok (import basarisiz) -- yanlis/tahmini bir FIFO uydurmaktansa
                # lots'u bosalt, build.py build_tax zaten (toplam uyusmuyor -> []) ayni
                # sonuca duser.
                pos["lots"] = []

    return {"symbol": sym, "side": side, "shares": shares, "price": price,
            "removed": removed,
            "new_shares": 0.0 if removed else pos["shares"],
            "new_avg_cost": old_avg if removed else pos["avg_cost"]}, None


def decision_record(tid, trade):
    """Karar gunlugu kaydi: 'neden aldim/sattim' notunu islemle birlikte kalici tutar.

    Tarih olarak islemin girildigi an (submitted_at) tercih edilir; yoksa bugun. Boylece
    kuyrukta bir gun bekleyen bir islem, gercekten karar verildigi gunle gunluge girer.
    """
    rec = {"id": tid, "date": trade_date_str(trade), "symbol": trade.get("symbol"),
           "side": trade.get("side"), "note": (trade.get("note") or "").strip()[:300]}
    try:
        rec["shares"] = round(float(trade.get("shares")), 6)
        rec["price"] = round(float(trade.get("price")), 4)
    except (TypeError, ValueError):
        pass
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("positions")
    ap.add_argument("trades")
    ap.add_argument("--out", default=None, help="Varsayilan: positions.json'un ustune yazar.")
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--quotes", default=None,
                     help="quotes.json (opsiyonel) -- verilirse yeni lot'larin price_eur'u fx.EURUSD ile hesaplanir.")
    a = ap.parse_args()
    out_path = a.out or a.positions

    cfg = json.load(open(a.positions, encoding="utf-8"))
    trades_doc = json.load(open(a.trades, encoding="utf-8"))
    trades = trades_doc.get("trades") or []

    fx = None
    if a.quotes:
        try:
            qdoc = json.load(open(a.quotes, encoding="utf-8"))
            fx = (qdoc.get("fx") or {}).get("EURUSD")
        except (OSError, ValueError):
            fx = None

    seen = list(cfg.get("processed_trade_ids") or [])
    seen_set = set(seen)

    decisions = list(cfg.get("decisions") or [])

    applied, errors, skipped = [], [], []
    for t in trades:
        tid = t.get("id")
        if tid and tid in seen_set:
            skipped.append(tid)
            continue
        result, err = apply_one(cfg["positions"], t, fx=fx)
        if err:
            errors.append({"id": tid, "symbol": t.get("symbol"), "reason": err})
        else:
            result["id"] = tid
            applied.append(result)
            decisions.append(decision_record(tid, t))
        if tid:
            seen.append(tid)

    cfg["processed_trade_ids"] = seen[-MAX_PROCESSED_IDS:]
    cfg["decisions"] = decisions[-MAX_DECISIONS:]

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(a.results, "w", encoding="utf-8") as f:
        json.dump({"applied": applied, "errors": errors, "skipped": skipped,
                   "decisions_added": len(applied)}, f, ensure_ascii=False, indent=1)

    print("ISLEM SONUCU: %d uygulandi, %d hata, %d zaten islenmisti (atlandi)" % (len(applied), len(errors), len(skipped)))
    for r in applied:
        tag = "KAPANDI" if r["removed"] else ("%.4g adet, ort. maliyet $%.4g" % (r["new_shares"], r["new_avg_cost"]))
        print("  + %s %s %.4g@$%.4g -> %s" % (r["symbol"], r["side"], r["shares"], r["price"], tag))
    for e in errors:
        print("  ! %s (%s): %s" % (e.get("symbol"), e.get("id"), e["reason"]))


if __name__ == "__main__":
    main()
