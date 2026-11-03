#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Portfoy Takibi -- veri ureticisi (data.js).

Kullanim:
    python3 build.py --mode aksam
    python3 build.py --mode sabah
    python3 build.py --mode pazar --narrative notes.json

Girdi : positions.json (pozisyonlar, tek dogruluk kaynagi)
        data.js        (onceki kosunun verisi -- history + anlati metinleri devralinir)
        notes.json     (opsiyonel; modelin yazdigi haber/makro/analist metinleri)
Cikti : data.js        (window.__PORTFOLIO__ = {...};)
        stdout         (kisa brief: hangi sembollerde haber yenilemesi gerekiyor)

Veri kaynagi: Yahoo Finance "spark" ucu -- TEK istekte 12 sembole kadar gunluk
kapanis + canli fiyat + 52 hafta aralik. Sembol basina tek tek istek atmak 429
(rate limit) yiyor, bu yuzden toplu istek kullaniliyor.
Istekler curl ile atilir: Yahoo, Python'un TLS imzasini 429 ile blokluyor,
curl ise 200 donuyor. curl yoksa urllib'e dusulur.

Yalnizca Python standart kutuphanesi kullanir. Ek paket gerekmez.
"""

import argparse, json, os, re, shutil, subprocess, sys, time
import urllib.error, urllib.request
from datetime import date, datetime, timezone

# DIKKAT: Yahoo, tam Chrome user-agent dizesini 429 ile blokluyor; kisa "Mozilla/5.0"
# tutarli sekilde 200 donuyor (16 Eylul 2026 testi: uzun UA 3/3 429, kisa UA 3/3 200).
# Kisitlama halinde sirayla denenir.
UA_LIST = ["Mozilla/5.0", "curl/8.7.1", "Mozilla/5.0 (X11; Linux x86_64)"]
SPARK = ("https://query1.finance.yahoo.com/v7/finance/spark"
         "?symbols={syms}&range={rng}&interval={iv}{extra}")
CHUNK = 18                  # tek istekte sembol sayisi (test: 19 sembol tek istekte sorunsuz)
CHUNK_SLEEP = 8.0           # istekler arasi bekleme (sn) -- Yahoo patlama (burst) halinde 429 veriyor
BACKOFF = [10, 25, 45]      # 429 sonrasi bekleme kademeleri

TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
TR_MONTHS_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz",
                   "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]

MOVE_ALERT_PCT = 5.0
RSI_HIGH, RSI_LOW = 70.0, 30.0
RANGE_EDGE_PCT = 5.0
HISTORY_KEEP = 30
SINCE_LAST_MIN_PCT = 2.0


# --------------------------------------------------------------------------- net

class Throttled(Exception):
    """Yahoo 429 -- beklenip tekrar denenmeli."""


def http_get(url, timeout=30, ua_index=0):
    """curl birincil, urllib yedek. Yahoo, Python TLS imzasini 429 ile blokluyor;
    ayrica fazla ek header gonderilince de 429 veriyor -- bu yuzden sadece UA."""
    body = None
    ua = UA_LIST[ua_index % len(UA_LIST)]
    curl = shutil.which("curl")
    if curl:
        p = subprocess.run(
            [curl, "-sS", "--max-time", str(timeout), "-H", "User-Agent: " + ua, url],
            capture_output=True)
        if p.returncode == 0 and p.stdout.strip():
            body = p.stdout
        else:
            sys.stderr.write("  (curl rc=%s: %s -- urllib deneniyor)\n"
                             % (p.returncode, p.stderr[:160].decode("utf-8", "replace")))
    if body is None:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise Throttled("urllib 429")
            raise
    head = body[:200].lstrip()
    if head[:1] not in (b"{", b"["):
        raise Throttled("JSON degil: %s" % head[:80].decode("utf-8", "replace"))
    return body


def fetch_spark(symbols, rng="1y", iv="1d", prepost=False, attempts=4):
    """{sembol: {ts, closes, meta}} dondurur. Alinamayan semboller sozlukte olmaz."""
    out = {}
    extra = "&includePrePost=true" if prepost else ""
    for i in range(0, len(symbols), CHUNK):
        group = symbols[i:i + CHUNK]
        url = SPARK.format(syms=",".join(group), rng=rng, iv=iv, extra=extra)
        for att in range(attempts):
            try:
                raw = json.loads(http_get(url, ua_index=att))
                if att:
                    sys.stderr.write("  (%s: %d. denemede geldi)\n" % (group[0], att + 1))
                for res in (raw.get("spark") or {}).get("result") or []:
                    sym = res.get("symbol")
                    resp = (res.get("response") or [{}])[0]
                    q = ((resp.get("indicators") or {}).get("quote") or [{}])[0]
                    ts_raw = resp.get("timestamp") or []
                    cl_raw = q.get("close") or []
                    ts, cl = [], []
                    for t, c in zip(ts_raw, cl_raw):
                        if c is None:
                            continue
                        ts.append(int(t))
                        cl.append(float(c))
                    if sym and cl:
                        out[sym] = {"ts": ts, "closes": cl, "meta": resp.get("meta") or {}}
                break
            except Throttled as e:
                wait = BACKOFF[min(att, len(BACKOFF) - 1)]
                sys.stderr.write("! Yahoo kısıtladı (%s): %s -- %ds bekleniyor\n"
                                 % (",".join(group[:3]) + ("..." if len(group) > 3 else ""), e, wait))
                if att + 1 < attempts:
                    time.sleep(wait)
            except Exception as e:
                sys.stderr.write("! spark hatası (%s): %s\n" % (",".join(group), e))
                if att + 1 < attempts:
                    time.sleep(5.0)
        time.sleep(CHUNK_SLEEP)
    missing = [s for s in symbols if s not in out]
    if missing:
        sys.stderr.write("! toplu istekte gelmeyen semboller: %s\n" % ", ".join(missing))
    return out


# ----------------------------------------------------------------- gostergeler

def sma_series(closes, n):
    out, run = [], 0.0
    for i, c in enumerate(closes):
        run += c
        if i >= n:
            run -= closes[i - n]
        out.append(round(run / n, 6) if i >= n - 1 else None)
    return out


def rsi_wilder(closes, n=14):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return round(100 - (100 / (1 + ag / al)), 1)


def pct(a, b):
    if not b:
        return None
    return round((a - b) / b * 100, 2)


def slice_6mo(ts, closes, days=182):
    cutoff = int(time.time()) - days * 86400
    idx = None
    for i, t in enumerate(ts):
        if t >= cutoff:
            idx = i
            break
    if idx is None:
        idx = max(0, len(ts) - 126)
    return ts[idx:], closes[idx:]


def tr_date(dt):
    return "%d %s %d, %02d:%02d" % (dt.day, TR_MONTHS[dt.month - 1], dt.year, dt.hour, dt.minute)


def tr_date_short(dt):
    return "%d %s" % (dt.day, TR_MONTHS_SHORT[dt.month - 1])


# ----------------------------------------------------------------- prev / narrative

def load_prev(path):
    if not path or not os.path.exists(path):
        return {}
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        sys.stderr.write("! onceki data.js ayrıştırılamadı, anlatı metinleri devralınamıyor\n")
        return {}


def merge_intel(base, delta):
    """Istihbarat blogu: alerts/calendar/radar delta'da varsa tumden degisir
    (rutin gecerliligini koruyanlari kendisi tasir); companies ve earnings (bilanco
    kartlari, Faz 18) sembol bazinda ezilir."""
    out = dict(base or {})
    if not delta:
        return out or None
    for key in ("alerts", "calendar", "radar", "updated_at"):
        if key in delta:
            out[key] = delta[key]
    for key in ("companies", "earnings"):
        if delta.get(key):
            merged = dict(out.get(key) or {})
            merged.update(delta[key])
            out[key] = merged
    return out


def merge_narrative(prev, notes):
    """Anlati metinleri: notes.json > prev > yok. notes yalnizca verdigi alanlari ezer."""
    nar = {
        "macro_summary": notes.get("macro_summary") or prev.get("macro_summary"),
        "macro_calendar": notes.get("macro_calendar") or prev.get("macro_calendar") or [],
        "positions": {}, "candidates": {},
        "intel": merge_intel(prev.get("intel"), notes.get("intel")),
    }
    prev_pos, note_pos = prev.get("positions") or {}, notes.get("positions") or {}
    for sym in set(list(prev_pos) + list(note_pos)):
        pn, nn = prev_pos.get(sym, {}), note_pos.get(sym, {})
        nar["positions"][sym] = {"why": nn.get("why") or pn.get("why"),
                                 "analyst": nn.get("analyst") or pn.get("analyst"),
                                 "health": nn.get("health")}
    prev_cand = {c["symbol"]: c for c in (prev.get("candidates") or []) if c.get("symbol")}
    note_cand = notes.get("candidates") or {}
    for sym in set(list(prev_cand) + list(note_cand)):
        nar["candidates"][sym] = {
            "analyst": (note_cand.get(sym) or {}).get("analyst") or prev_cand.get(sym, {}).get("analyst"),
            "health": (note_cand.get(sym) or {}).get("health") or prev_cand.get(sym, {}).get("health")}
    return nar


# ----------------------------------------------------------------- ana hesap

def build_position(pos, bulk, pre, prev_pos):
    """(kayit, taze_mi) dondurur; veri yoksa (None, False)."""
    sym = pos["symbol"]
    if not bulk or len(bulk["closes"]) < 60:
        if prev_pos:
            rec = dict(prev_pos)
            rec.update({"stale": True, "data_source": "önceki koşu",
                        "shares": float(pos["shares"]), "est_avg_cost": round(float(pos["avg_cost"]), 4)})
            # adet/maliyet degistiyse tutarlar yine dogru olsun
            price = rec.get("current_price")
            if price:
                mv, cb = rec["shares"] * price, rec["shares"] * rec["est_avg_cost"]
                rec.update({"market_value": round(mv, 2), "cost_basis": round(cb, 2),
                            "gain_loss_usd": round(mv - cb, 2),
                            "gain_loss_pct": round((mv - cb) / cb * 100, 2) if cb else None})
            return rec, False
        return None, False

    ts, closes, meta = bulk["ts"], list(bulk["closes"]), bulk["meta"]
    price = float(meta.get("regularMarketPrice") or closes[-1])
    closes[-1] = price                      # canli fiyati serinin sonuna koy

    day_pct = meta.get("regularMarketChangePercent")
    if day_pct is None:
        base = meta.get("chartPreviousClose") or (closes[-2] if len(closes) > 1 else price)
        day_pct = pct(price, float(base)) or 0.0
    day_pct = round(float(day_pct), 2)

    ts6, cl6 = slice_6mo(ts, closes)
    sma20s = sma_series(cl6, 20)
    sma20 = sma_series(closes, 20)[-1]
    sma50 = sma_series(closes, 50)[-1] if len(closes) >= 50 else None

    hi = float(meta.get("fiftyTwoWeekHigh") or max(closes[-252:]))
    lo = float(meta.get("fiftyTwoWeekLow") or min(closes[-252:]))
    pos52 = round((price - lo) / ((hi - lo) or 1) * 100, 1)

    shares, avg = float(pos["shares"]), float(pos["avg_cost"])
    mv, cb = shares * price, shares * avg
    rsi = rsi_wilder(closes)

    if sma20 and sma50 and price > sma20 and sma20 > sma50:
        trend = "Yukselis"
    elif sma20 and sma50 and price < sma20 and sma20 < sma50:
        trend = "Dusus"
    else:
        trend = "Karisik"
    rsi_label = ("Aşırı Alım" if (rsi or 50) > RSI_HIGH
                 else "Aşırı Satım" if (rsi or 50) < RSI_LOW else "Nötr")

    rec = {
        "name": pos.get("name", sym),
        "shares": shares,
        "est_avg_cost": round(avg, 4),
        "avg_cost_is_estimate": bool(pos.get("avg_cost_is_estimate")),
        "current_price": round(price, 2),
        "day_change_pct": day_pct,
        "market_value": round(mv, 2),
        "cost_basis": round(cb, 2),
        "gain_loss_usd": round(mv - cb, 2),
        "gain_loss_pct": round((mv - cb) / cb * 100, 2) if cb else None,
        "week52_high": round(hi, 2),
        "week52_low": round(lo, 2),
        "sma20": round(sma20, 4) if sma20 else None,
        "sma50": round(sma50, 4) if sma50 else None,
        "sma20_series": [round(v, 4) if v is not None else None for v in sma20s],
        "rsi14": rsi,
        "closes_6mo": [round(c, 2) for c in cl6],
        "timestamps_6mo": ts6,
        "sector": pos.get("sector", "-"),
        "trend": trend,
        "rsi_label": rsi_label,
        "chg_1y_pct": pct(price, closes[0]) if len(closes) > 200 else (prev_pos or {}).get("chg_1y_pct"),
        "chg_6mo_pct": pct(price, cl6[0]),
        "pos_in_52w_range_pct": pos52,
        "data_source": "yahoo",
        "stale": False,
    }
    if pre and pre.get("closes"):
        base = (pre["meta"] or {}).get("chartPreviousClose")
        last = pre["closes"][-1]
        if base:
            rec["premarket_price"] = round(last, 2)
            rec["premarket_change_pct"] = pct(last, float(base))
    return rec, True


def build_position_from_quote(pos, q, prev_pos, now):
    """Ag yok: fiyati verilen quote'tan al, tarihsel seriyi onceki data.js'ten devral.
    Bugunun noktasini serinin sonuna ekler (ayni gune ikinci kez calisirsa gunceller)."""
    sym = pos["symbol"]
    ser = q.get("series")
    if not ser and (not prev_pos or not prev_pos.get("closes_6mo")):
        return None, False
    price = q.get("price")
    if price is None:
        if not prev_pos:
            return None, False
        rec = dict(prev_pos)
        rec.update({"stale": True, "data_source": "önceki koşu"})
        return rec, False
    price = float(price)

    if ser and ser.get("closes"):
        # Tam 6 aylik seri quotes dosyasindan geldi -- en dogru yol.
        closes = [float(c) for c in ser["closes"]]
        ts = [int(t) for t in (ser.get("ts") or range(len(closes)))]
        closes[-1] = price
    else:
        # Seri yok: onceki data.js'in serisine bugunun noktasini ekle.
        closes = list(prev_pos["closes_6mo"])
        ts = list(prev_pos.get("timestamps_6mo") or [])
        if len(ts) != len(closes):
            ts = list(range(len(closes)))
        now_ts = int(now.timestamp())
        if ts and datetime.fromtimestamp(ts[-1], timezone.utc).date() == now.date():
            closes[-1], ts[-1] = price, now_ts
        else:
            closes.append(price)
            ts.append(now_ts)
        if len(closes) > 130:                   # ~6 ay
            closes, ts = closes[-130:], ts[-130:]

    sma20s = sma_series(closes, 20)
    sma20 = sma20s[-1]
    pp = prev_pos or {}
    sma50 = sma_series(closes, 50)[-1] if len(closes) >= 50 else pp.get("sma50")
    rsi = rsi_wilder(closes)

    hi = float(q.get("week52_high") or pp.get("week52_high") or max(closes))
    lo = float(q.get("week52_low") or pp.get("week52_low") or min(closes))
    pos52 = round((price - lo) / ((hi - lo) or 1) * 100, 1)

    shares, avg = float(pos["shares"]), float(pos["avg_cost"])
    mv, cb = shares * price, shares * avg

    if sma20 and sma50 and price > sma20 and sma20 > sma50:
        trend = "Yukselis"
    elif sma20 and sma50 and price < sma20 and sma20 < sma50:
        trend = "Dusus"
    else:
        trend = "Karisik"
    rsi_label = ("Aşırı Alım" if (rsi or 50) > RSI_HIGH
                 else "Aşırı Satım" if (rsi or 50) < RSI_LOW else "Nötr")

    day_pct = q.get("day_change_pct")
    if day_pct is None and len(closes) > 1:
        day_pct = pct(price, closes[-2])
    rec = {
        "name": pos.get("name", sym), "shares": shares,
        "est_avg_cost": round(avg, 4),
        "avg_cost_is_estimate": bool(pos.get("avg_cost_is_estimate")),
        "current_price": round(price, 2),
        "day_change_pct": round(float(day_pct or 0), 2),
        "market_value": round(mv, 2), "cost_basis": round(cb, 2),
        "gain_loss_usd": round(mv - cb, 2),
        "gain_loss_pct": round((mv - cb) / cb * 100, 2) if cb else None,
        "week52_high": round(hi, 2), "week52_low": round(lo, 2),
        "sma20": round(sma20, 4) if sma20 else None,
        "sma50": round(sma50, 4) if sma50 else None,
        "sma20_series": [round(v, 4) if v is not None else None for v in sma20s],
        "rsi14": rsi, "closes_6mo": [round(c, 2) for c in closes], "timestamps_6mo": ts,
        "sector": pos.get("sector", "-"), "trend": trend, "rsi_label": rsi_label,
        "chg_1y_pct": q.get("chg_1y_pct", pp.get("chg_1y_pct")),
        "chg_6mo_pct": pct(price, closes[0]),
        "pos_in_52w_range_pct": pos52,
        "data_source": "quotes.json", "stale": False,
    }
    if q.get("premarket_price") is not None:
        rec["premarket_price"] = round(float(q["premarket_price"]), 2)
        rec["premarket_change_pct"] = q.get("premarket_change_pct")
    return rec, True


def build_candidate(c, bulk, prev_by_sym):
    sym = c["symbol"]
    if not bulk or not bulk["closes"]:
        old = prev_by_sym.get(sym)
        if old:
            old = dict(old)
            old["stale"] = True
            return old
        return {"symbol": sym, "sector": c.get("sector", "-")}
    closes, meta = list(bulk["closes"]), bulk["meta"]
    price = float(meta.get("regularMarketPrice") or closes[-1])
    closes[-1] = price
    hi = float(meta.get("fiftyTwoWeekHigh") or max(closes))
    lo = float(meta.get("fiftyTwoWeekLow") or min(closes))
    _, cl6 = slice_6mo(bulk["ts"], closes)
    return {
        "symbol": sym, "sector": c.get("sector", "-"),
        "current_price": round(price, 2),
        "chg_1y_pct": pct(price, closes[0]),
        "chg_6mo_pct": pct(price, cl6[0]) if cl6 else None,
        "week52_high": round(hi, 2), "week52_low": round(lo, 2),
        "pos_in_52w_range_pct": round((price - lo) / ((hi - lo) or 1) * 100, 1),
    }


def merge_notes_only(a):
    """Ag gerektirmeyen mod: mevcut data.js'teki sayilara dokunmadan yalnizca
    anlati metinlerini (haber notu, makro ozet, takvim, analist) gunceller.
    Bulut rutini boylece fiyat cekmeden haber notlarini isleyip yayinlayabilir."""
    if not a.narrative:
        sys.stderr.write("HATA: --merge-notes ile --narrative <dosya> zorunlu.\n")
        sys.exit(2)
    payload = load_prev(a.prev)
    if not payload or not payload.get("positions"):
        sys.stderr.write("HATA: %s okunamadi veya bos; birlestirilecek veri yok.\n" % a.prev)
        sys.exit(1)
    notes = json.load(open(a.narrative, encoding="utf-8"))

    touched = []
    for sym, n in (notes.get("positions") or {}).items():
        if sym not in payload["positions"]:
            sys.stderr.write("! %s portfoyde yok, notu atlandi\n" % sym)
            continue
        if n.get("why"):
            payload["positions"][sym]["why"] = n["why"]
            touched.append(sym)
        if n.get("analyst"):
            payload["positions"][sym]["analyst"] = n["analyst"]
        ph = position_health(n.get("health"), datetime.now(timezone.utc))
        if ph:
            payload["positions"][sym]["health"] = ph
    if notes.get("macro_summary"):
        payload["macro_summary"] = notes["macro_summary"]
    if notes.get("macro_calendar"):
        payload["macro_calendar"] = notes["macro_calendar"]
    if notes.get("intel"):
        payload["intel"] = merge_intel(payload.get("intel"), notes["intel"])
    if notes.get("since_last_text") and payload.get("since_last"):
        payload["since_last"]["text"] = notes["since_last_text"]
    if notes.get("candidates"):
        by_sym = {c.get("symbol"): c for c in payload.get("candidates") or []}
        for sym, n in notes["candidates"].items():
            for k in ("analyst", "health"):
                if sym in by_sym and n.get(k):
                    by_sym[sym][k] = n[k]
        sc = build_scorecard(payload.get("candidates") or [], datetime.now(timezone.utc))
        if sc:
            payload["scorecard"] = sc

    if notes.get("briefs") and payload.get("stock_notes"):
        now_ = datetime.now(timezone.utc)
        for it in payload["stock_notes"].get("items") or []:
            nb = notes["briefs"].get(it.get("symbol"))
            if isinstance(nb, dict) and nb:
                it["brief"] = build_stock_notes_brief(nb, now_)

    payload["notes_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    with open(a.out, "w", encoding="ascii") as f:
        f.write("window.__PORTFOLIO__ = ")
        json.dump(payload, f, ensure_ascii=True, separators=(",", ":"))
        f.write(";\n")
    print("NOTLAR ISLENDI -> %s" % a.out)
    print("  haber notu guncellenen: %s" % (", ".join(touched) or "yok"))
    print("  istihbarat: %s" % ("evet (%d uyari)" % len((notes.get("intel") or {}).get("alerts") or [])
                                 if notes.get("intel") else "hayir"))
    print("  makro ozet: %s | takvim: %s | since_last metni: %s"
          % ("evet" if notes.get("macro_summary") else "hayir",
             "evet" if notes.get("macro_calendar") else "hayir",
             "evet" if notes.get("since_last_text") else "hayir"))
    print("  sayisal veri DEGISTIRILMEDI (uretim: %s)" % payload.get("generated_at"))


def add_tax_notes(positions, cfg, fx, now):
    """Faz 11: her pozisyona kucuk vergi notu verisi (euro). Almanya kurallari tax_de.py'de.

    Gercek alim kayitlari (lots) varsa maliyet alis gunu kuruyla, yoksa tahmini ortalama
    maliyet bugunku kurla euroya cevrilir ve 'estimate' isaretlenir. Not 'tek basina
    tamamini bugun satsan' varsayimiyla hesaplanir; yilin kalan vergisiz hakki
    positions.json -> tax_profile.realized_stock_gain_ytd_eur ile dusulur.
    """
    try:
        import tax_de
    except ImportError:
        sys.stderr.write("! tax_de.py yok -- vergi notu atlandi\n")
        return None
    prof = tax_de.profile(cfg.get("tax_profile"))
    rate_now = float(fx["rate"])
    ytd = float(prof.get("realized_stock_gain_ytd_eur") or 0)
    allowance_left = max(0.0, float(prof["allowance"]) - max(ytd, 0.0))
    eff = tax_de.stock_rate(prof)
    by_sym = {p["symbol"]: p for p in cfg["positions"]}
    for sym, rec in positions.items():
        pos = by_sym.get(sym) or {}
        price = rec.get("current_price")
        if not price:
            continue
        shares = float(pos.get("shares") or rec.get("shares") or 0)
        lots = pos.get("lots") or []
        # Yeni bir islem lots'a islenmediyse adetler tutmaz -> tahmine dus, yanlis rakam gosterme.
        if lots and abs(sum(float(l["shares"]) for l in lots) - shares) > 1e-6:
            lots = []
        if lots:
            cost = 0.0
            for l in lots:
                pe = l.get("price_eur")
                if pe is None:
                    r, _ = tax_de.fx_on(datetime.strptime(l["date"], "%Y-%m-%d").date(), fx)
                    pe = float(l["price_usd"]) / r
                cost += float(l["shares"]) * float(pe)
            estimate = False
        else:
            cost = shares * float(pos.get("avg_cost") or 0) / rate_now
            estimate = True
        value = shares * float(price) / rate_now
        gain = value - cost
        tax = max(0.0, gain - allowance_left) * eff if gain > 0 else 0.0
        rec["tax"] = {
            "cost_eur": round(cost, 2), "value_eur": round(value, 2),
            "gain_eur": round(gain, 2), "tax_if_sold_eur": round(tax, 2),
            "net_if_sold_eur": round(gain - tax, 2), "estimate": estimate,
            "first_buy": min(l["date"] for l in lots) if lots else None,
        }
    return {"year": now.year, "allowance_left_eur": round(allowance_left, 2),
            "rate": round(eff, 5), "eurusd": rate_now,
            "broker_withholds": bool(prof.get("withholds_tax"))}


def build_crypto(cfg, qdoc, now, stocks_eur):
    """Faz 13a: kripto pozisyonlari (euro) + toplam servet. Fiyat yoksa None."""
    items = cfg.get("crypto") or []
    prices = (qdoc or {}).get("crypto") or {}
    if not items or not prices:
        return None, None
    try:
        import tax_de
    except ImportError:
        tax_de = None
    today = now.date()
    out = []
    for c in items:
        q = prices.get(c["symbol"])
        if not q or not q.get("price"):
            continue
        price = float(q["price"])
        lots = c.get("lots") or []
        amount = sum(float(l["amount"]) for l in lots)
        cost = sum(float(l["amount"]) * float(l["price_eur"]) for l in lots)
        lot_rows = []
        for l in lots:
            st = tax_de.crypto_lot_status(l, price, today) if tax_de else {}
            lot_rows.append({"date": l["date"], "amount": round(float(l["amount"]), 8),
                             "gain_eur": st.get("gain_eur"), "taxfree_on": st.get("taxfree_on"),
                             "is_taxfree": st.get("is_taxfree"), "days_to_taxfree": st.get("days_to_taxfree"),
                             "reform_note": st.get("reform_note")})
        taxfree_amount = sum(r["amount"] for r in lot_rows if r.get("is_taxfree"))
        pending = [r for r in lot_rows if r.get("is_taxfree") is False]
        out.append({
            "symbol": c["symbol"], "coin": c.get("coin") or c["symbol"].split("-")[0],
            "name": c.get("name") or c["symbol"], "amount": round(amount, 8),
            "price_eur": round(price, 2), "value_eur": round(amount * price, 2),
            "cost_eur": round(cost, 2), "gain_eur": round(amount * price - cost, 2),
            "gain_pct": round((amount * price - cost) / cost * 100, 2) if cost else None,
            "day_change_pct": q.get("day_change_pct"), "chg_1y_pct": q.get("chg_1y_pct"),
            "chg_1w_pct": q.get("chg_1w_pct"), "chg_1m_pct": q.get("chg_1m_pct"),
            "week52_high": q.get("week52_high"), "week52_low": q.get("week52_low"),
            "stale": bool(q.get("stale")),
            "taxfree_amount": round(taxfree_amount, 8),
            "next_taxfree": min((r["taxfree_on"] for r in pending), default=None),
            "lots": lot_rows,
        })
        if c.get("thesis"):
            out[-1]["thesis"] = {"text": c["thesis"], "breaks_if": c.get("breaks_if"),
                                 "date": c.get("thesis_date")}
        if c.get("exit"):
            out[-1]["exit"] = exit_status(price, c["exit"])
    if not out:
        return None, None
    crypto_eur = sum(x["value_eur"] for x in out)
    total = stocks_eur + crypto_eur
    wealth = {"stocks_eur": round(stocks_eur, 2), "crypto_eur": round(crypto_eur, 2),
              "total_eur": round(total, 2),
              "crypto_pct": round(crypto_eur / total * 100, 1) if total else None}
    return out, wealth

def build_allocation(cfg, positions, crypto, wealth):
    """Faz 13b: Vedat'in kendi sinirlarina gore dagilim aynasi (kural motoru degil)."""
    rules = cfg.get("rules") or {}
    if not rules or not wealth or not wealth.get("total_eur"):
        return None
    total = wealth["total_eur"]
    by_sym = {p["symbol"]: p for p in cfg["positions"]}
    assets = [(s, (r.get("tax") or {}).get("value_eur") or 0) for s, r in positions.items()]
    assets += [(c["coin"], c["value_eur"]) for c in (crypto or [])]
    big_sym, big_val = max(assets, key=lambda a: a[1])
    spec = sum(v for s, v in assets if (by_sym.get(s) or {}).get("bucket") == "spekulatif")
    items = []
    def add(key, label, val_pct, lim, who=None):
        if lim is None:
            return
        items.append({"key": key, "label": label, "value_pct": round(val_pct, 1),
                      "limit_pct": lim, "over": val_pct > lim, "who": who})
    add("single", "Tek varlık", big_val / total * 100, rules.get("max_single_pct"), big_sym)
    over_single = [s for s, v in assets if v / total * 100 > (rules.get("max_single_pct") or 999)]
    if items:
        items[-1]["over_list"] = over_single
    add("crypto", "Kripto", wealth["crypto_eur"] / total * 100, rules.get("max_crypto_pct"))
    add("spec", "Riskli sepet", spec / total * 100, rules.get("max_speculative_pct"))
    return {"items": items}

EXIT_NEAR_PCT = 5.0


def exit_status(price, ex):
    """Faz 14b: Vedat'in onceden yazdigi kar al / zarar kes seviyesine gore durum (emir degil)."""
    if not ex or not price:
        return None
    tp, st = ex.get("take_profit"), ex.get("stop")
    out = {"take_profit": tp, "stop": st, "currency": ex.get("currency", "USD"),
           "tp_basis": ex.get("tp_basis"), "set_date": ex.get("set_date"), "status": None}
    if tp:
        out["to_tp_pct"] = round((float(tp) / price - 1) * 100, 1)
    if st:
        out["to_stop_pct"] = round((float(st) / price - 1) * 100, 1)
    if tp and price >= float(tp):
        out["status"] = "tp"
    elif st and price <= float(st):
        out["status"] = "stop"
    elif tp and out["to_tp_pct"] <= EXIT_NEAR_PCT:
        out["status"] = "near_tp"
    elif st and out["to_stop_pct"] >= -EXIT_NEAR_PCT:
        out["status"] = "near_stop"
    return out


BENCH_LABELS = {"SPY": "S&P 500", "URTH": "MSCI World"}


def build_perf(cfg, qdoc, positions, crypto, fx):
    """Faz 13d: euro getirisinin kaynagi (hisse / kur) + ayni gun ayni parayla endeks kiyasi.

    Yalniz gercek alim kaydi (lots) olan pozisyonlar sayilir. Endeks: her alimin $ tutari o gunun
    kapanisindan endekse konmus sayilir, bugunku fiyat ve kurla euroya cevrilir (temettu haric).
    Seri alim tarihinden once baslamiyorsa o endeks atlanir. Veri yoksa None.
    """
    try:
        import tax_de
    except ImportError:
        return None
    benches = (qdoc or {}).get("benchmarks") or {}
    if not fx or not fx.get("rate"):
        return None
    rate_now = float(fx["rate"])
    by_sym = {p["symbol"]: p for p in cfg["positions"]}
    day = lambda d: datetime.strptime(d, "%Y-%m-%d").date()

    flows = []  # (tarih, usd_tutar) -- endeks kiyasi icin, hisse + kripto
    cost = const_fx = value = 0.0
    stock_dates = []
    for sym, rec in positions.items():
        pos = by_sym.get(sym) or {}
        lots = pos.get("lots") or []
        price = rec.get("current_price")
        if not lots or not price or (rec.get("tax") or {}).get("estimate", True):
            continue
        for l in lots:
            sh, pu = float(l["shares"]), float(l["price_usd"])
            pe = l.get("price_eur")
            fxb = pu / float(pe) if pe else tax_de.fx_on(day(l["date"]), fx)[0]
            cost += sh * pu / fxb
            const_fx += sh * float(price) / fxb
            value += sh * float(price) / rate_now
            flows.append((l["date"], sh * pu, "s"))
            stock_dates.append(l["date"])
    if not stock_dates:
        return None
    crypto_cost = crypto_value = 0.0
    by_coin = {c["symbol"]: c for c in (crypto or [])}
    for c in cfg.get("crypto") or []:
        live = by_coin.get(c["symbol"])
        if not live:
            continue
        crypto_value += float(live["value_eur"])
        for l in c.get("lots") or []:
            eur = float(l["amount"]) * float(l["price_eur"])
            crypto_cost += eur
            flows.append((l["date"], eur * tax_de.fx_on(day(l["date"]), fx)[0], "c"))

    first = min(f[0] for f in flows)
    bench = []
    for b, ser in benches.items():
        if not ser.get("ts") or ser.get("stale"):
            continue
        if ser["ts"][0] > int((day(first) - date(1970, 1, 1)).total_seconds()):
            continue
        cur = float(ser.get("price") or ser["closes"][-1])
        st = tot = 0.0
        for d, usd, kind in flows:
            units = usd / tax_de.fx_on(day(d), ser)[0]
            v = units * cur / rate_now
            tot += v
            if kind == "s":
                st += v
        bench.append({"sym": b, "label": BENCH_LABELS.get(b, b),
                      "stocks_eur": round(st, 2), "total_eur": round(tot, 2)})
    return {
        "since": first, "stocks_since": min(stock_dates),
        "stocks": {"cost_eur": round(cost, 2), "value_eur": round(value, 2),
                   "gain_eur": round(value - cost, 2),
                   "stock_effect_eur": round(const_fx - cost, 2),
                   "fx_effect_eur": round(value - const_fx, 2)},
        "total_cost_eur": round(cost + crypto_cost, 2),
        "total_value_eur": round(value + crypto_value, 2),
        "bench": bench,
    }

def build_goal(cfg, positions, crypto, wealth, fx, now):
    """Faz 14: '31 Aralik'a kadar +X EUR' hedef panosu + risk butcesi aynasi (emir degil).

    Kazanc = bugunku servet - baslangic servet + baslangictan beri islemlerin net nakdi
    (satis girer, alis cikar; decisions listesinden, bugunku kurla). Kripto islemleri kayitli degil.
    Risk: her varlik son 52 haftanin dibine donerse kaybedilecek EUR; toplam butceyle karsilastirilir.
    """
    g = cfg.get("goal") or {}
    if not g.get("start_value_eur") or not wealth or not fx or not fx.get("rate"):
        return None
    rate = float(fx["rate"])
    day = lambda d: datetime.strptime(d, "%Y-%m-%d").date()
    start, end = day(g["start_date"]), day(g["end_date"])
    cash = 0.0
    for d in cfg.get("decisions") or []:
        if (d.get("date") or "")[:10] < g["start_date"]:
            continue
        amt = float(d.get("shares") or 0) * float(d.get("price") or 0) / rate
        cash += amt if d.get("side") == "sell" else -amt
    total = float(wealth["total_eur"])
    gain = total - float(g["start_value_eur"]) + cash
    target = float(g["target_eur"])
    days_left = max(0, (end - now.date()).days)
    weeks_left = days_left / 7.0
    need_total = float(g["start_value_eur"]) + target - cash
    weekly = None
    if weeks_left >= 1 and total > 0 and need_total > total:
        weekly = round(((need_total / total) ** (1 / weeks_left) - 1) * 100, 2)

    # varliklar: (ad, deger_eur, fiyat, 52h_dip, 52h_zirve) -- fiyat/aralik ayni para biriminde
    assets = []
    for sym, r in positions.items():
        v = (r.get("tax") or {}).get("value_eur")
        if v and r.get("current_price"):
            assets.append((sym, float(v), r["current_price"], r.get("week52_low"), r.get("week52_high"), "s"))
    for c in crypto or []:
        assets.append((c["coin"], float(c["value_eur"]), c["price_eur"], c.get("week52_low"), c.get("week52_high"), "c"))

    risk_rows = []
    for name, v, px, lo, hi, _k in assets:
        if lo and px:
            risk_rows.append({"name": name, "value_eur": round(v, 2),
                              "loss_eur": round(max(0.0, v * (1 - float(lo) / float(px))), 2)})
    risk_rows.sort(key=lambda r: -r["loss_eur"])
    risk_total = sum(r["loss_eur"] for r in risk_rows)
    budget = g.get("risk_budget_eur")

    def scen(label, crypto_to, stock_mult):
        val = 0.0
        for name, v, px, lo, hi, k in assets:
            if k == "c":
                ref = {"dip": lo, "now": px, "zirve": hi}[crypto_to] or px
                val += v * float(ref) / float(px)
            else:
                val += v * stock_mult
        return {"label": label, "gain_eur": round(val - float(g["start_value_eur"]) + cash, 2)}
    scenarios = [scen("Kötü: kripto 52h dibine, hisseler −%15", "dip", 0.85),
                 scen("Orta: kripto aynı, hisseler aynı", "now", 1.0),
                 scen("İyi: kripto 52h zirvesine, hisseler +%15", "zirve", 1.15)]
    return {
        "start_date": g["start_date"], "end_date": g["end_date"],
        "start_value_eur": g["start_value_eur"], "target_eur": target,
        "value_eur": round(total, 2), "net_cash_eur": round(cash, 2),
        "gain_eur": round(gain, 2), "progress_pct": round(gain / target * 100, 1) if target else None,
        "left_eur": round(target - gain, 2), "days_left": days_left,
        "weekly_needed_pct": weekly, "scenarios": scenarios,
        "risk": {"budget_eur": budget, "total_eur": round(risk_total, 2),
                 "over": bool(budget) and risk_total > float(budget), "rows": risk_rows[:6]},
    }


# ----------------------------------------------------------------- Faz 21: hisse notlari
NOTE_CANDIDATES = 5          # karnede en yuksek puanli kac aday not alir
NOTE_STALE_DAYS = 8          # haftalik not bu kadar gunden eskiyse "eski" sayilir
CAND_STOP_PCT = 18.0         # aday icin varsayilan zarar kes mesafesi (mevcut kurallarla ayni)
NOTE_RISK_MIN_FREE = 100.0   # bundan az bos risk "neredeyse dolu" sayilir (EUR)


def chg_since(ts, closes, days):
    """Serinin son noktasina gore 'days' gun onceki kapanistan bu yana % degisim."""
    if not ts or not closes or len(ts) != len(closes):
        return None
    cutoff = ts[-1] - days * 86400
    base = None
    for t, c in zip(ts, closes):
        if t > cutoff:
            break
        base = c
    if not base:
        return None
    return round((closes[-1] - base) / base * 100, 2)


def _eur(x):
    return "{:,.0f}".format(x).replace(",", ".") + " €"


def build_stock_notes_brief(b, now):
    """Rutinin yazdigi notu tasir; yasini ve 'eski' bayragini bugune gore hesaplar."""
    b = dict(b)
    try:
        age = max(0, (now.date() - datetime.strptime((b.get("asof") or "")[:10], "%Y-%m-%d").date()).days)
        b["age_days"] = age
        b["stale"] = age > NOTE_STALE_DAYS
    except ValueError:
        b["stale"] = True
    return b


def build_stock_notes(cfg, positions, order, crypto, wealth, scorecard, candidates, briefs, prev_notes, now):
    """Faz 21: her pozisyon, ETH ve karnenin ilk adaylari icin kisa not.

    Iki katman: (1) 'rules' -- Vedat'in KENDI kurallarindan (exit, sinirlar, risk butcesi)
    hesaplanan euro rakamlari; deterministik, emir degil, ayna. (2) 'brief' -- rutinin yazdigi
    kaynakli metin (gundem, CEO, alis/satis riski, karsi arguman); notes.json -> briefs.SEMBOL.
    Notsuz koşuda onceki data.js'teki brief tasinir, yalnizca 'eski' bayragi yenilenir.
    """
    total = float((wealth or {}).get("total_eur") or 0)
    rules = cfg.get("rules") or {}
    goal = cfg.get("goal") or {}
    budget = goal.get("risk_budget_eur")
    lim_single = rules.get("max_single_pct")
    lim_crypto = rules.get("max_crypto_pct")
    lim_spec = rules.get("max_speculative_pct")
    by_cfg = {p["symbol"]: p for p in cfg["positions"]}
    prev_items = {i["symbol"]: i for i in ((prev_notes or {}).get("items") or []) if i.get("symbol")}

    def brief_for(sym):
        b = (briefs or {}).get(sym) or (prev_items.get(sym) or {}).get("brief")
        return build_stock_notes_brief(b, now) if b else None

    # zarar kes seviyelerine gore kullanilan risk (EUR): deger x (1 - stop/fiyat)
    used_risk, risk_known = 0.0, False
    rows = []
    for sym in order:
        r = positions[sym]
        v = (r.get("tax") or {}).get("value_eur")
        ex = r.get("exit") or {}
        if v and ex.get("stop") and r.get("current_price"):
            used_risk += max(0.0, float(v) * (1 - float(ex["stop"]) / float(r["current_price"])))
            risk_known = True
        rows.append(("pozisyon", sym, r))
    for c in crypto or []:
        ex = c.get("exit") or {}
        if ex.get("stop") and c.get("price_eur"):
            used_risk += max(0.0, float(c["value_eur"]) * (1 - float(ex["stop"]) / float(c["price_eur"])))
            risk_known = True
        rows.append(("kripto", c["coin"], c))
    free_risk = (float(budget) - used_risk) if (budget and risk_known) else None

    spec_eur = sum((positions[s].get("tax") or {}).get("value_eur") or 0 for s in order
                   if (by_cfg.get(s) or {}).get("bucket") == "spekulatif")
    crypto_eur = float((wealth or {}).get("crypto_eur") or 0)

    items = []
    for kind, sym, r in rows:
        if kind == "pozisyon":
            v = float((r.get("tax") or {}).get("value_eur") or 0)
            price, cur = r.get("current_price"), "USD"
            w1 = chg_since(r.get("timestamps_6mo"), r.get("closes_6mo"), 7)
            m1 = chg_since(r.get("timestamps_6mo"), r.get("closes_6mo"), 30)
            name = r.get("name") or sym
            bucket = (by_cfg.get(sym) or {}).get("bucket")
        else:
            v = float(r.get("value_eur") or 0)
            price, cur = r.get("price_eur"), "EUR"
            w1, m1 = r.get("chg_1w_pct"), r.get("chg_1m_pct")
            name, bucket = r.get("name") or sym, "kripto"
        lo, hi = r.get("week52_low"), r.get("week52_high")
        pos52 = r.get("pos_in_52w_range_pct")
        if pos52 is None and lo and hi and price and hi > lo:
            pos52 = round((float(price) - float(lo)) / (float(hi) - float(lo)) * 100, 1)
        share = v / total * 100 if total else None
        lines = []
        ex = r.get("exit") or {}
        money = (lambda n: "$" + "{:,.2f}".format(float(n))) if cur == "USD" else (lambda n: _eur(float(n)))
        if ex.get("status") in ("tp", "near_tp"):
            lines.append({"tone": "warn", "text": "Kâr al seviyen %s — fiyat %s." % (
                money(ex["take_profit"]), "bu seviyede ya da üstünde" if ex["status"] == "tp" else "%5'ten yakın")})
        if ex.get("status") in ("stop", "near_stop"):
            lines.append({"tone": "bad", "text": "Zarar kes seviyen %s — fiyat %s." % (
                money(ex["stop"]), "bu seviyede ya da altında" if ex["status"] == "stop" else "%5'ten yakın")})
        if lim_single and share is not None and share > lim_single:
            over = v - lim_single / 100.0 * total
            held = r.get("amount") if kind == "kripto" else r.get("shares")
            units = ""
            if held and v:
                units = " (≈ %s adet)" % (("%.4f" if kind == "kripto" else "%.2f") % (over / v * float(held))).replace(".", ",")
            lines.append({"tone": "warn", "text": "Tek varlık sınırın %%%s; bu varlığın servetteki payı %%%s. Sınırın üstündeki kısım ≈ %s%s." % (
                lim_single, ("%.1f" % share).replace(".", ","), _eur(over), units)})
        if kind == "kripto" and lim_crypto and total and crypto_eur / total * 100 > lim_crypto:
            lines.append({"tone": "warn", "text": "Kripto sınırın %%%s; kriptonun servetteki payı %%%s. Sınırın üstündeki kısım ≈ %s." % (
                lim_crypto, ("%.1f" % (crypto_eur / total * 100)).replace(".", ","),
                _eur(crypto_eur - lim_crypto / 100.0 * total))})
        if bucket == "spekulatif" and lim_spec and total and spec_eur / total * 100 > lim_spec:
            lines.append({"tone": "warn", "text": "Riskli sepet sınırın (%%%s) aşılmış; bu hissenin sepetteki payı %%%s." % (
                lim_spec, ("%.0f" % (v / spec_eur * 100)) if spec_eur else "?")})
        if ex.get("stop") and price:
            loss = max(0.0, v * (1 - float(ex["stop"]) / float(price)))
            lines.append({"tone": "info", "text": "Zarar kes seviyesine (%s) inerse kayıp ≈ %s." % (money(ex["stop"]), _eur(loss))})
        items.append({"kind": kind, "symbol": sym, "name": name, "price": price, "currency": cur,
                      "chg_1w_pct": w1, "chg_1m_pct": m1, "chg_1y_pct": r.get("chg_1y_pct"),
                      "pos_in_52w_range_pct": pos52, "week52_low": lo, "week52_high": hi,
                      "value_eur": round(v, 2), "share_pct": round(share, 1) if share is not None else None,
                      "rules": lines, "brief": brief_for(sym)})

    by_cand = {c.get("symbol"): c for c in candidates or []}
    top = [r_ for r_ in ((scorecard or {}).get("rows") or [])][:NOTE_CANDIDATES]
    for row in top:
        sym = row["symbol"]
        c = by_cand.get(sym) or {}
        lines = [{"tone": "info", "text": "Aday karnesi puanı %s/100." % row.get("score")}]
        if free_risk is not None:
            if free_risk < NOTE_RISK_MIN_FREE:
                lines.append({"tone": "warn", "text": "Risk bütçen (%s) zarar kes seviyelerine göre neredeyse dolu (boş ≈ %s). Kuralına göre bu hisseye yer açmak için önce başka bir pozisyonda risk azaltılmalı: örneğin 1.000 €'luk bir alım %%%d zarar kesle ≈ %s risk ister." % (
                    _eur(float(budget)), _eur(max(0.0, free_risk)), int(CAND_STOP_PCT), _eur(1000 * CAND_STOP_PCT / 100.0))})
            else:
                cap = free_risk / (CAND_STOP_PCT / 100.0)
                cap_single = lim_single / 100.0 * total if (lim_single and total) else None
                amt = min(cap, cap_single) if cap_single else cap
                lines.append({"tone": "info", "text": "Risk bütçende boş ≈ %s. %%%d zarar kesle kurallarına sığan üst sınır ≈ %s%s." % (
                    _eur(free_risk), int(CAND_STOP_PCT), _eur(amt),
                    " (tek varlık sınırıyla kısıtlı)" if cap_single and cap_single < cap else "")})
        items.append({"kind": "aday", "symbol": sym, "name": row.get("name") or sym,
                      "price": c.get("current_price"), "currency": "USD",
                      "chg_1w_pct": c.get("chg_1w_pct"), "chg_1m_pct": c.get("chg_1m_pct"),
                      "chg_1y_pct": c.get("chg_1y_pct"), "pos_in_52w_range_pct": c.get("pos_in_52w_range_pct"),
                      "week52_low": c.get("week52_low"), "week52_high": c.get("week52_high"),
                      "score": row.get("score"), "rules": lines, "brief": brief_for(sym)})

    return {"items": items,
            "risk": {"budget_eur": budget, "used_eur": round(used_risk, 2) if risk_known else None,
                     "free_eur": round(free_risk, 2) if free_risk is not None else None},
            "candidate_symbols": [r_["symbol"] for r_ in top]}


def merge_briefs(prev, delta):
    """briefs sembol bazinda tumden degisir (bir sembolun notu yarim guncellenmez)."""
    out = dict(prev or {})
    for sym, b in (delta or {}).items():
        if isinstance(b, dict) and b:
            out[sym] = b
    return out


# ----------------------------------------------------------------- Faz 14c: aday karnesi
def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def score_candidate(c):
    """Tek aday icin 0-100 karne puani. Saglik 60 (4 soru x 15) + momentum 20 + analist 20.

    Saglik verisi (health) pazar rutininin kaynakli arastirmasindan gelir; yoksa None doner
    (puanlanmamis aday siralamaya girmez). Siralamadir, oneri degildir.
    """
    h = c.get("health") or {}
    if not h or not h.get("profit"):
        return None
    q1 = {"kar": 15, "faaliyet_kari": 8, "zarar": 0}.get(h.get("profit"), 0)
    if h.get("fcf_positive"):
        q2 = 15
    else:
        cy = h.get("cash_years")
        q2 = 0 if cy is None else (12 if cy >= 3 else 6 if cy >= 1.5 else 0)
    sh = h.get("share_change_pct")
    q3 = 7 if sh is None else (15 if sh <= 0 else 10 if sh <= 3 else 4 if sh <= 10 else 0)
    tc = h.get("top_customer_pct")
    q4 = 7 if tc is None else (15 if tc <= 10 else 8 if tc <= 25 else 0)
    health = q1 + q2 + q3 + q4
    m6 = c.get("chg_6mo_pct")
    p52 = c.get("pos_in_52w_range_pct")
    mom = 0.0
    if m6 is not None:
        mom += (_clamp(m6, -30, 30) + 30) / 60 * 12
    if p52 is not None:
        mom += _clamp(p52, 0, 100) / 100 * 8
    an = c.get("analyst") or {}
    up = None
    ana = 0.0
    if an.get("target") and c.get("current_price"):
        up = round((float(an["target"]) / float(c["current_price"]) - 1) * 100, 1)
        ana = (_clamp(up, -10, 40) + 10) / 50 * 20
    return {"score": round(health + mom + ana), "health": health, "momentum": round(mom),
            "analyst": round(ana), "upside_pct": up, "q": [q1, q2, q3, q4]}


def _health_answers(h):
    prof = {"kar": "Kâr ediyor", "faaliyet_kari": "Faaliyet kârı var, net zarar",
            "zarar": "Zarar ediyor"}.get(h.get("profit"), "?")
    if h.get("fcf_positive"):
        cash = "Nakit üretiyor"
    elif h.get("cash_years") is not None:
        cash = "Nakit ≈ %s yıl yeter" % ("%.1f" % h["cash_years"]).replace(".", ",")
    else:
        cash = "Nakit süresi bilinmiyor"
    sh = h.get("share_change_pct")
    shares = "Hisse sayısı ?" if sh is None else (
        "Hisse sayısı 12 ayda %s%%" % ("%+.1f" % sh).replace(".", ","))
    tc = h.get("top_customer_pct")
    cust = "En büyük müşteri: açıklanmıyor" if tc is None else (
        "Hiçbir müşteri %10'u geçmiyor" if tc < 10 else "En büyük müşteri %%%s" % ("%g" % tc).replace(".", ","))
    return [prof, cash, shares, cust]


def build_scorecard(candidates, now, top=10, stale_days=45):
    rows = []
    for c in candidates or []:
        sc = score_candidate(c)
        if not sc:
            continue
        h = c["health"]
        age = None
        try:
            age = (now.date() - datetime.strptime(h.get("asof", "")[:10], "%Y-%m-%d").date()).days
        except ValueError:
            pass
        rows.append({"symbol": c["symbol"], "sector": c.get("sector"), "name": h.get("name"),
                     "score": sc["score"], "parts": {"saglik": sc["health"], "momentum": sc["momentum"],
                                                     "analist": sc["analyst"]},
                     "q": sc["q"], "answers": _health_answers(h), "upside_pct": sc["upside_pct"],
                     "chg_6mo_pct": c.get("chg_6mo_pct"), "note": h.get("note"),
                     "asof": h.get("asof"), "stale": age is not None and age > stale_days,
                     "source_url": h.get("source_url"), "source_label": h.get("source_label")})
    if not rows:
        return None
    rows.sort(key=lambda r: (-r["score"], r["symbol"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"scored": len(rows), "total": len(candidates or []), "rows": rows[:top],
            "updated": max((r["asof"] or "") for r in rows) or None}


def position_health(h, now, stale_days=45):
    """Faz 14d (13e): kucuk sirket pozisyonlari icin karnenin ayni dort saglik sorusu.
    Veri notes.json -> positions.SEMBOL.health (pazar rutini). Puan yok; yalnizca cevap + renk (q)."""
    if not h or not h.get("profit"):
        return None
    age = None
    try:
        age = (now.date() - datetime.strptime(h.get("asof", "")[:10], "%Y-%m-%d").date()).days
    except ValueError:
        pass
    return {"answers": _health_answers(h), "q": score_candidate({"health": h})["q"],
            "first_profit": h.get("first_profit"), "note": h.get("note"), "asof": h.get("asof"),
            "stale": age is not None and age > stale_days,
            "source_url": h.get("source_url"), "source_label": h.get("source_label")}


def carry_health(ph, now, stale_days=45):
    """Notsuz kosu (--mode ... --quotes, narrative yok): onceki data.js'teki saglik satirini tasir,
    yalnizca 'eski' bayragini bugune gore yeniden hesaplar."""
    if not ph or not ph.get("answers"):
        return None
    out = dict(ph)
    try:
        out["stale"] = (now.date() - datetime.strptime((ph.get("asof") or "")[:10], "%Y-%m-%d").date()).days > stale_days
    except ValueError:
        pass
    return out


# ----------------------------------------------------------------- Faz 14d: ucret sayaci
def build_fees_ytd(cfg, fx, now):
    """Faz 14d (13f): bu yil hisse islemlerine odenen ucret (EUR). Kripto haric.

    Bilinen: tax_profile.fees_paid_ytd_eur / fees_paid_trades (Revolut dokumunden; yil tutmali).
    Tahmin: bu yilin decisions kayitlari -- fee_eur varsa o, yoksa tutar x commission_pct
    (tax_de profili, %0,12: ucretsiz kotayi asan emir icin ust sinir)."""
    tp = cfg.get("tax_profile") or {}
    year = now.year
    known = float(tp.get("fees_paid_ytd_eur") or 0) if tp.get("year") == year else 0.0
    known_n = int(tp.get("fees_paid_trades") or 0) if tp.get("year") == year else 0
    try:
        import tax_de
        pct = float(tax_de.profile(tp)["commission_pct"])
    except ImportError:
        pct = 0.0012
    rate = float(fx["rate"]) if fx and fx.get("rate") else None
    est = 0.0
    est_n = exact_n = 0
    for d in cfg.get("decisions") or []:
        if not (d.get("date") or "").startswith(str(year)):
            continue
        if d.get("fee_eur") is not None:
            known += float(d["fee_eur"])
            exact_n += 1
        elif rate:
            est += float(d.get("shares") or 0) * float(d.get("price") or 0) / rate * pct
            est_n += 1
    if not (known_n or exact_n or est_n) and "fees_paid_ytd_eur" not in tp:
        return None
    return {"year": year, "known_eur": round(known, 2), "known_trades": known_n + exact_n,
            "estimated_eur": round(est, 2), "estimated_trades": est_n,
            "total_eur": round(known + est, 2), "commission_pct": pct}


# ----------------------------------------------------------------- Faz 14c: vergi hatirlatmalari
def _tr_day(d):
    return "%d %s %d" % (d.day, TR_MONTHS[d.month - 1], d.year)


def build_tax_reminders(cfg, positions, crypto, tax_info, fx, now, window=7):
    """Tarihi kesin iki kazanc: son ETH parcasinin vergisiz olmasi ve Aralik'ta yanacak
    1.000 EUR yillik hak. Tutarlar tax_de.py ile. notify = tarih 0-7 gun icinde (bildirim).
    Ikinci deger (Faz 15): yillik vergi tablosu -- bu yila kadar gerceklesen hisse kari,
    vergiye tabi tutar, kenara ayrilacak tahmini vergi (Revolut kesmiyor)."""
    try:
        import tax_de
    except ImportError:
        return None, None
    today = now.date()
    out = []

    for c in crypto or []:
        pend = [l for l in c.get("lots") or [] if l.get("is_taxfree") is False]
        if not pend:
            continue
        d = min(datetime.strptime(l["taxfree_on"], "%Y-%m-%d").date() for l in pend)
        amt = sum(l["amount"] for l in pend if l["taxfree_on"] == d.isoformat())
        gain = sum(l.get("gain_eur") or 0 for l in pend if l["taxfree_on"] == d.isoformat())
        last = len({l["taxfree_on"] for l in pend}) == 1
        days = (d - today).days
        num = lambda x, f="%.2f": (f % x).replace(".", ",")
        bought = min(l["date"] for l in pend if l["taxfree_on"] == d.isoformat())
        out.append({
            "id": "crypto-%s-%s" % (c["coin"], d.isoformat()), "date": d.isoformat(),
            "days_left": days, "notify": 0 <= days <= window,
            "title": "%s: %s %s parçası vergisiz" % (_tr_day(d), "son" if last else "sıradaki", c["coin"]),
            "text": ("%s tarihinde alınan %s %s o gün 1 yılı doldurur%s. Bugün satılsa bu parçanın "
                     "kârı %s € (kısa vadeli kripto kârı yılda toplam 1.000 €'nun altındaysa zaten vergi yok)."
                     % (datetime.strptime(bought, "%Y-%m-%d").strftime("%d.%m.%Y"), num(amt, "%.4f"),
                        c["coin"],
                        "; o günden sonra elindeki %s %s'nin tamamı vergisiz satılabilir" % (
                            num(c["amount"], "%.4f"), c["coin"]) if last else "",
                        num(gain, "%+.0f"))),
            "short": "%s gun sonra (%s) %s %s parcasi vergisiz" % (
                days, d.strftime("%d.%m"), "son" if last else "siradaki", c["coin"]),
        })

    left = float(tax_info.get("allowance_left_eur") or 0)
    if left > 1 and today.year == int(tax_info.get("year") or today.year):
        prof = tax_de.profile(cfg.get("tax_profile"))
        by_sym = {p["symbol"]: p for p in cfg["positions"]}
        cands = []
        for sym, r in positions.items():
            lots = (by_sym.get(sym) or {}).get("lots")
            if not lots or not r.get("current_price"):
                continue
            gps = tax_de.gain_eur_per_share_marginal(lots, float(r["current_price"]), fx, today)
            cands.append({"symbol": sym, "shares": float(r["shares"]), "price_usd": float(r["current_price"]),
                          "gain_per_share_eur": gps, "eurusd": float(fx["rate"])})
        plan = tax_de.harvest_allowance(cands, left, prof)
        used = sum(x["gain_eur"] for x in plan)
        saved = sum(x["tax_saved_eur"] for x in plan)
        fee = sum(x["fee_eur"] for x in plan)
        for x in plan:
            t = (positions.get(x["symbol"]) or {}).get("tax")
            if t is not None:
                t["harvest"] = {"shares": x["shares"], "gain_eur": x["gain_eur"],
                                "tax_saved_eur": x["tax_saved_eur"], "fee_eur": x["fee_eur"]}
        steps = ", ".join("%s %s adet (kâr ≈%s €)" % (x["symbol"], ("%g" % x["shares"]).replace(".", ","),
                                                      ("%.0f" % x["gain_eur"])) for x in plan)
        base = ("Kalan vergisiz hakkın %s € ve 31 Aralık'ta yanar. " % ("{:,.0f}".format(left).replace(",", ".")))
        if plan:
            base += ("Hesap (bugünkü fiyatla): kârdaki hissede sat + aynı gün geri al → "
                     "%s. Pozisyon aynı kalır, alış maliyeti yükselir; ileride ≈%s € daha az vergi, "
                     "ücret ≈%s €. Aynı gün geri alış Almanya'da kötüye kullanım sayılmıyor (BFH IX R 5/16). "
                     "Takas 1 iş günü: işlem en geç 29 Aralık. Karar senin."
                     % (steps, "%.0f" % saved, "%.0f" % fee))
        else:
            base += "Bugün kârdaki hisselerde ücretten büyük fayda sağlayan bir sat-geri al hesabı çıkmıyor."
        for d, title, short in (
                (datetime(today.year, 12, 1).date(), "yıllık 1.000 € vergisiz hak",
                 "Aralik'ta %s EUR vergisiz hak yanacak (hesap: ~%s EUR vergi farki)"),
                (datetime(today.year, 12, 22).date(), "vergisiz hakkın son haftası",
                 "vergisiz hakkin son haftasi, %s EUR kullanilmadi (~%s EUR)")):
            days = (d - today).days
            out.append({"id": "allowance-%s" % d.isoformat(), "date": d.isoformat(), "days_left": days,
                        "notify": 0 <= days <= window, "title": "%s: %s" % (_tr_day(d), title),
                        "text": base, "plan": plan, "tax_saved_eur": round(saved, 2),
                        "fee_eur": round(fee, 2), "gain_used_eur": round(used, 2),
                        "short": short % ("%.0f" % left, "%.0f" % saved)})
        # yalnizca bir sonraki Aralik kaydi sayfada kalsin; 1 Aralik gectiyse 22 Aralik gorunur
        al = [x for x in out if x["id"].startswith("allowance")]
        keep = [x for x in al if x["days_left"] >= 0][:1]
        out = [x for x in out if not x["id"].startswith("allowance")] + keep
    out = [x for x in out if x["days_left"] >= 0]
    out.sort(key=lambda x: x["date"])

    tax_plan = None
    if today.year == int(tax_info.get("year") or today.year):
        prof2 = tax_de.profile(cfg.get("tax_profile"))
        realized = float(prof2.get("realized_stock_gain_ytd_eur") or 0)
        annual = tax_de.annual_stock_tax(max(0.0, realized), max(0.0, -realized), prof=prof2)
        tax_plan = {
            "year": today.year,
            "realized_gain_ytd_eur": round(realized, 2),
            "allowance_eur": float(prof2["allowance"]),
            "allowance_left_eur": round(left, 2),
            "taxable_ytd_eur": annual["taxable"],
            "tax_owed_ytd_eur": annual["tax"],
            "broker_withholds": bool(prof2.get("withholds_tax")),
        }
    return out or None, tax_plan


def build_cash(cfg, positions, wealth, qdoc):
    """Nakit hedefi + Senaryo B satis plani + rejim aynasi (emir degil)."""
    c = cfg.get("cash")
    if not c:
        return None
    stocks_eur = sum((p.get("tax") or {}).get("value_eur") or 0 for p in positions.values())
    total = (wealth or {}).get("total_eur") or stocks_eur
    total += c.get("cash_eur") or 0
    if not total:
        return None
    target = total * c["min_cash_pct"] / 100
    by_sym = {p["symbol"]: p for p in cfg["positions"]}
    rows, plan_eur, plan_gain = [], 0.0, 0.0
    for it in c.get("plan") or []:
        p, pos = by_sym.get(it["symbol"]), positions.get(it["symbol"])
        if not p or not pos:
            continue
        val = (pos.get("tax") or {}).get("value_eur") or 0
        sh = p["shares"] * it["fraction"]
        left, cost = sh, 0.0
        for lot in sorted(p["lots"], key=lambda l: l["date"]):
            take = min(left, lot["shares"])
            cost += take * lot["price_eur"]
            left -= take
        v = val * it["fraction"]
        rows.append({"symbol": it["symbol"], "fraction": it["fraction"], "shares": round(sh, 4),
                     "value_eur": round(v, 2), "gain_eur": round(v - cost, 2), "why": it.get("why")})
        plan_eur += v
        plan_gain += v - cost
    signals = []
    spy = ((qdoc.get("benchmarks") or {}).get("SPY") or {}).get("closes") or []
    spy = [x for x in spy if x]
    if len(spy) >= 220:
        m200, m200_prev = sum(spy[-200:]) / 200, sum(spy[-220:-20]) / 200
        peak = max(spy[-252:])
        signals += [
            {"name": "SPY 200 günlük ortalamanın altında", "bear": spy[-1] < m200},
            {"name": "200 günlük ortalama eğimi aşağı", "bear": m200 < m200_prev},
            {"name": "SPY zirveden %10'dan fazla düştü", "bear": spy[-1] < peak * 0.9}]
    man = c.get("regime_manual") or {}
    th = c.get("thresholds") or {}
    rg = qdoc.get("regime") or {}
    vix = (rg.get("^VIX") or {}).get("price")
    tnx = (rg.get("^TNX") or {}).get("price")
    live = bool(vix or tnx)
    if vix is not None:
        signals.append({"name": "VIX %.1f (eşik %s)" % (vix, th.get("vix", 25)), "bear": vix > th.get("vix", 25)})
    if tnx is not None:
        signals.append({"name": "10Y faiz %%%.2f (eşik %%%s)" % (tnx, th.get("us10y", 5.0)), "bear": tnx > th.get("us10y", 5.0)})
    else:
        signals.append({"name": "Faiz şoku (10Y %s)" % man.get("us10y_pct"), "bear": bool(man.get("rate_shock")), "manual": True})
    irx = [x for x in ((rg.get("^IRX") or {}).get("closes") or []) if x]
    if len(irx) >= 60:
        d = irx[-1] - irx[0]
        signals.append({"name": "Fed sıkılaştırıyor (3 aylık tahvil faizi %+.2f puan, ~3 ay)" % d, "bear": d >= th.get("fed_rise", 0.25)})
    else:
        signals.append({"name": "Fed sıkılaştırıyor (%s)" % man.get("fed_range"), "bear": bool(man.get("fed_tightening")), "manual": True})
    bear = sum(1 for s in signals if s["bear"])
    band = next((b["cash_pct"] for b in c.get("bands") or [] if bear <= b["max_bear"]), c["min_cash_pct"])
    cash_now = c.get("cash_eur") or 0
    lim = (cfg.get("rules") or {}).get("max_crypto_pct")
    crypto_eur = (wealth or {}).get("crypto_eur") or 0
    eth = None
    if lim and crypto_eur:
        eth = {"value_eur": round(crypto_eur, 2), "pct": round(crypto_eur / total * 100, 1), "limit_pct": lim,
               "excess_eur": round(max(0, crypto_eur - lim / 100 * total), 2)}
    eth_plan = None
    ep = c.get("eth_plan")
    if ep and eth and crypto_eur:
        held_units = sum(l["amount"] for cc in cfg.get("crypto") or [] for l in cc.get("lots") or [])
        px = crypto_eur / held_units if held_units else 0
        cum, rows_ = 0.0, []
        for st in ep["steps"]:
            cum += st["eur"]
            units = st["eur"] / px if px else 0
            rows_.append({"label": st["label"], "when": st["when"], "eur": st["eur"], "eth": round(units, 3),
                          "pct_of_holdings": round(units / held_units * 100, 1),
                          "eth_pct_after": round((crypto_eur - cum) / total * 100, 1)})
        eth_plan = {"name": ep.get("name"), "steps": rows_, "note": ep.get("note"),
                    "total_eur": round(cum, 2), "price_eur": round(px, 2)}
    return {"eth_plan": eth_plan, "eth": eth, "cash_eur": round(cash_now, 2), "cash_pct": round(cash_now / total * 100, 1),
            "min_cash_pct": c["min_cash_pct"], "total_eur": round(total, 2),
            "target_eur": round(target, 2), "gap_eur": round(max(0, target - cash_now), 2),
            "plan_name": c.get("plan_name"), "plan": rows, "plan_eur": round(plan_eur, 2),
            "plan_gain_eur": round(plan_gain, 2),
            "plan_pct_after": round((cash_now + plan_eur) / total * 100, 1),
            "signals": signals, "bear_count": bear, "signal_count": len(signals),
            "band_pct": band, "regime_live": live, "regime_asof": (qdoc.get("asof") or "")[:10] if live else man.get("asof"), "regime_note": man.get("note")}


def _watch_times(date_s, et):
    from zoneinfo import ZoneInfo
    approx = et.endswith("~")
    hh, mm = (int(x) for x in et.rstrip("~").split(":"))
    y, m, d = (int(x) for x in date_s.split("-"))
    ny = datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))
    out = {"et": "%02d:%02d" % (hh, mm), "approx": approx}
    for key, tz in (("de", "Europe/Berlin"), ("tr", "Europe/Istanbul")):
        t = ny.astimezone(ZoneInfo(tz))
        out[key] = t.strftime("%H:%M") + (" (+1 gün)" if t.date() > ny.date() else "")
    return out


def build_watch(cfg, now):
    w = cfg.get("watch") or {}
    today = now.date() if hasattr(now, "date") else now
    out = []
    for e in w.get("events") or []:
        y, m, d = (int(x) for x in e["date"].split("-"))
        days = (datetime(y, m, d).date() - today).days
        if days >= 0:
            item = dict(e, days_left=days)
            if e.get("et"):
                try:
                    item["times"] = _watch_times(e["date"], e["et"])
                except Exception:
                    item["times"] = None
            out.append(item)
    out.sort(key=lambda e: e["date"])
    return out[:20] or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sabah", "aksam", "pazar"], default=None,
                    help="Zorunlu (--merge-notes kullanilmadigi surece).")
    ap.add_argument("--positions", default="positions.json")
    ap.add_argument("--prev", default="data.js")
    ap.add_argument("--narrative", default=None)
    ap.add_argument("--out", default="data.js")
    ap.add_argument("--brief", default=None)
    ap.add_argument("--merge-notes", action="store_true",
                    help="Ag erisimi YOK modu: fiyat cekmez, hesap yapmaz. Mevcut data.js'i okur, "
                         "--narrative dosyasindaki haber/makro/analist metinlerini isler ve geri yazar.")
    ap.add_argument("--quotes", default=None,
                    help="Ag erisimi olmayan ortamlar icin: fiyatlari bu JSON dosyasindan al "
                         "(sekil: {\"quotes\": {\"TSLA\": {\"price\": 1.2, \"day_change_pct\": 0.5, "
                         "\"week52_high\": 9, \"week52_low\": 1, \"chg_1y_pct\": 3}}}). "
                         "Tarihsel seri onceki data.js'ten devralinir.")
    a = ap.parse_args()

    if a.merge_notes:
        return merge_notes_only(a)
    if not a.mode:
        sys.stderr.write("HATA: --mode zorunlu (sabah | aksam | pazar).\n")
        sys.exit(2)

    cfg = json.load(open(a.positions, encoding="utf-8"))
    prev = load_prev(a.prev)
    notes = json.load(open(a.narrative, encoding="utf-8")) if a.narrative else {}
    nar = merge_narrative(prev, notes)
    prev_pos = prev.get("positions") or {}
    now = datetime.now(timezone.utc)

    syms = [p["symbol"] for p in cfg["positions"]]
    quotes = {}
    qdoc = {}
    if a.quotes:
        qdoc = json.load(open(a.quotes, encoding="utf-8"))
        quotes = qdoc.get("quotes") or qdoc          # duz sozluk de kabul
        bulk, pre = {}, {}
        sys.stderr.write("quotes modu: ag erisimi kullanilmiyor, fiyatlar %s dosyasindan\n" % a.quotes)
    else:
        bulk = fetch_spark(syms, "1y", "1d")
        pre = fetch_spark(syms, "1d", "5m", prepost=True) if a.mode == "sabah" else {}

    positions, order, failed = {}, [], []
    for p in cfg["positions"]:
        sym = p["symbol"]
        if a.quotes:
            q = dict(quotes.get(sym) or {})
            ser = (qdoc.get("series") or {}).get(sym)
            if ser and ser.get("closes"):
                q["series"] = ser
            rec, fresh = build_position_from_quote(p, q, prev_pos.get(sym), now)
        else:
            rec, fresh = build_position(p, bulk.get(sym), pre.get(sym), prev_pos.get(sym))
        if rec is None:
            failed.append(sym)
            continue
        n = nar["positions"].get(sym) or {}
        if n.get("why"):
            rec["why"] = n["why"]
        if n.get("analyst"):
            rec["analyst"] = n["analyst"]
        ph = position_health(n.get("health"), now) or carry_health((prev_pos.get(sym) or {}).get("health"), now)
        if ph:
            rec["health"] = ph
        positions[sym] = rec
        order.append(sym)
        if not fresh:
            failed.append(sym)

    # ---- tez karti (Faz 13c): positions.json'daki thesis/breaks_if aynen tasinir
    for p_cfg in cfg["positions"]:
        rec = positions.get(p_cfg["symbol"])
        if rec is not None and p_cfg.get("thesis"):
            rec["thesis"] = {"text": p_cfg["thesis"], "breaks_if": p_cfg.get("breaks_if"),
                             "date": p_cfg.get("thesis_date")}
        if rec is not None and p_cfg.get("exit"):
            rec["exit"] = exit_status(rec.get("current_price"), p_cfg["exit"])

    # ---- vergi notu (Faz 11): yalnizca quotes.json'da kur varsa
    tax_info = None
    fx = (qdoc.get("fx") or {}).get("EURUSD") if qdoc else None
    if fx and fx.get("rate"):
        tax_info = add_tax_notes(positions, cfg, fx, now)

    fresh_count = sum(1 for p in positions.values() if not p.get("stale"))
    if fresh_count < len(cfg["positions"]) / 2:
        sys.stderr.write("HATA: pozisyonların yarısından fazlası taze veri alamadı "
                         "(%d/%d). Yayın iptal.\n" % (fresh_count, len(cfg["positions"])))
        sys.exit(1)

    # ---- sektor dagilimi + ozet
    tot_val = sum(p["market_value"] for p in positions.values())
    tot_cost = sum(p["cost_basis"] for p in positions.values())
    agg = {}
    for p in positions.values():
        agg[p["sector"]] = agg.get(p["sector"], 0) + p["market_value"]
    sectors = [{"name": k, "value": round(v, 2), "pct": round(v / tot_val * 100, 1)}
               for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]

    # ---- yapay zeka temasi (Faz 16, Bolum 10 son maddesi): positions.json'da ai_theme:true
    # isaretli pozisyonlar -- kendi tezinde AI'yi ana deger surucusu olarak yazanlar. AI'dan
    # dolayli yararlanan ama kendisi AI sirketi olmayanlar (ör. Oklo'nun veri merkezi elektrigi,
    # Tesla'nin surucusuz taksisi) bilinçli olarak disarida -- sektor dagiliminda ayrica görünüyor.
    by_sym_cfg = {p["symbol"]: p for p in cfg["positions"]}
    ai_syms = [s for s in positions if by_sym_cfg.get(s, {}).get("ai_theme")]
    ai_theme = None
    if ai_syms:
        ai_val = sum(positions[s]["market_value"] for s in ai_syms)
        ai_theme = {
            "pct": round(ai_val / tot_val * 100, 1) if tot_val else 0,
            "value": round(ai_val, 2),
            "positions": [
                {"symbol": s, "name": positions[s].get("name", s),
                 "pct": round(positions[s]["market_value"] / tot_val * 100, 1) if tot_val else 0}
                for s in sorted(ai_syms, key=lambda s: -positions[s]["market_value"])
            ],
        }

    day_usd = sum(p["market_value"] - p["market_value"] / (1 + p["day_change_pct"] / 100)
                  for p in positions.values() if p.get("day_change_pct") is not None)
    ranked = sorted(positions.items(), key=lambda kv: kv[1].get("gain_loss_pct") or 0)
    summary = {
        "total_value": round(tot_val, 2), "total_cost": round(tot_cost, 2),
        "total_gain": round(tot_val - tot_cost, 2),
        "total_gain_pct": round((tot_val - tot_cost) / tot_cost * 100, 2) if tot_cost else 0,
        "day_change_usd": round(day_usd, 2),
        "day_change_pct": round(day_usd / (tot_val - day_usd) * 100, 2) if (tot_val - day_usd) else 0,
        "best": ranked[-1][0], "worst": ranked[0][0], "position_count": len(positions),
    }

    # ---- uyarilar
    alerts = []
    for sym, p in positions.items():
        if p.get("stale"):
            alerts.append({"sym": sym, "kind": "stale", "value": None,
                           "detail": "Güncel fiyat alınamadı, önceki koşunun verisi gösteriliyor"})
            continue
        d = p.get("day_change_pct")
        if d is not None and abs(d) >= MOVE_ALERT_PCT:
            alerts.append({"sym": sym, "kind": "move", "value": d,
                           "detail": "Günlük %s%.2f%% hareket" % ("+" if d > 0 else "", d)})
        r = p.get("rsi14")
        if r is not None and (r >= RSI_HIGH or r <= RSI_LOW):
            alerts.append({"sym": sym, "kind": "rsi", "value": r,
                           "detail": "RSI %.1f — %s bölgesi" % (r, "aşırı alım" if r >= RSI_HIGH else "aşırı satım")})
        q = p.get("pos_in_52w_range_pct")
        if q is not None and (q >= 100 - RANGE_EDGE_PCT or q <= RANGE_EDGE_PCT):
            alerts.append({"sym": sym, "kind": "range52", "value": q,
                           "detail": "52 hafta %s ucunda (%%%s)" % ("zirve" if q > 50 else "dip", q)})
    rank_kind = {"move": 0, "stale": 1, "range52": 2, "rsi": 3}
    alerts.sort(key=lambda x: (rank_kind.get(x["kind"], 9), -abs(x["value"] or 0)))

    # ---- son rapordan bu yana
    since = None
    if prev_pos:
        rows = []
        for sym, p in positions.items():
            old = prev_pos.get(sym)
            if not old or not old.get("current_price") or p.get("stale"):
                continue
            ch = pct(p["current_price"], old["current_price"])
            if ch is not None and abs(ch) >= SINCE_LAST_MIN_PCT:
                rows.append({"sym": sym, "pct": ch,
                             "from": old["current_price"], "to": p["current_price"]})
        rows.sort(key=lambda r: -abs(r["pct"]))
        prev_total = (prev.get("summary") or {}).get("total_value")
        since = {
            "prev_ts": prev.get("generated_at_iso"),
            "prev_label": prev.get("generated_at"),
            "prev_run_kind": prev.get("run_kind"),
            "portfolio_pct": pct(tot_val, prev_total) if prev_total else None,
            "portfolio_from": prev_total, "portfolio_to": round(tot_val, 2),
            "per_symbol": rows[:8],
            "text": notes.get("since_last_text") or None,
        }

    # ---- rapor gecmisi
    history = list(prev.get("history") or [])
    if a.mode in ("aksam", "pazar"):
        movers = sorted([{"sym": s, "pct": p["day_change_pct"]} for s, p in positions.items()
                         if p.get("day_change_pct") is not None and not p.get("stale")],
                        key=lambda m: -abs(m["pct"]))[:3]
        entry = {"ts": now.isoformat(timespec="minutes"), "date": now.strftime("%Y-%m-%d"),
                 "label": tr_date_short(now), "run_kind": a.mode,
                 "total_value": round(tot_val, 2), "total_cost": round(tot_cost, 2),
                 "total_gl_pct": summary["total_gain_pct"],
                 "day_change_pct": summary["day_change_pct"], "top_movers": movers}
        if history and history[-1].get("date") == entry["date"]:
            history[-1] = entry
        else:
            history.append(entry)
        history = history[-HISTORY_KEEP:]

    # ---- aday hisseler (yalnizca pazar kosusunda yeniden cekilir)
    prev_cand = {c["symbol"]: c for c in (prev.get("candidates") or []) if c.get("symbol")}
    if a.mode == "pazar" and cfg.get("candidates") and not a.quotes:
        cand_syms = [c["symbol"] for c in cfg["candidates"]]
        cbulk = fetch_spark(cand_syms, "1y", "1d")
        candidates = []
        for c in cfg["candidates"]:
            rec = build_candidate(c, cbulk.get(c["symbol"]), prev_cand)
            cn = nar["candidates"].get(c["symbol"]) or {}
            for k in ("analyst", "health"):
                if cn.get(k):
                    rec[k] = cn[k]
            candidates.append(rec)
    elif a.quotes and qdoc.get("candidates"):
        cand_notes = nar["candidates"]
        candidates = []
        for c in qdoc["candidates"]:
            rec = dict(c)
            cn = cand_notes.get(c.get("symbol")) or {}
            for k in ("analyst", "health"):
                if cn.get(k):
                    rec[k] = cn[k]
            candidates.append(rec)
    else:
        candidates = prev.get("candidates") or []

    payload = {
        "schema": 2, "generated_at": tr_date(now),
        "generated_at_iso": now.isoformat(timespec="minutes"),
        "run_kind": a.mode, "order": order, "positions": positions,
        "sectors": sectors, "ai_theme": ai_theme, "summary": summary, "alerts": alerts,
        "since_last": since, "history": history, "candidates": candidates,
        "macro_summary": nar["macro_summary"], "macro_calendar": nar["macro_calendar"],
        "intel": nar["intel"],
        "failed": sorted(set(failed)),
        "processed_trade_ids": cfg.get("processed_trade_ids") or [],
        "decisions": cfg.get("decisions") or [],
        "radar_prices": (qdoc.get("radar") if qdoc.get("radar") is not None
                         else prev.get("radar_prices")) or {},
    }
    if tax_info:
        payload["tax_info"] = tax_info
        stocks_eur = sum((p.get("tax") or {}).get("value_eur") or 0 for p in positions.values())
        crypto, wealth = build_crypto(cfg, qdoc, now, stocks_eur)
        if crypto:
            payload["crypto"] = crypto
            payload["wealth"] = wealth
            alloc = build_allocation(cfg, positions, crypto, wealth)
            if alloc:
                payload["allocation"] = alloc
        perf = build_perf(cfg, qdoc, positions, crypto, fx)
        if perf:
            payload["perf"] = perf
        goal = build_goal(cfg, positions, crypto, wealth, fx, now)
        if goal:
            payload["goal"] = goal
        cash_info = build_cash(cfg, positions, wealth, qdoc)
        if cash_info:
            payload["cash"] = cash_info
    watch = build_watch(cfg, now)
    for c_ in payload.get("crypto") or []:
        for l_ in c_.get("lots") or []:
            if l_.get("days_to_taxfree") and l_["days_to_taxfree"] > 0:
                ev_ = {"date": l_["taxfree_on"], "title": "%s lotu vergisiz olur (%s alımı, %s adet)"
                       % (c_["coin"], l_["date"], round(l_["amount"], 4)),
                       "tickers": c_["coin"], "status": "teyitli", "source": "Kendi alım kaydın",
                       "days_left": l_["days_to_taxfree"]}
                watch = sorted((watch or []) + [ev_], key=lambda e: e["date"])
    if watch:
        payload["watch"] = watch
        if (cfg.get("watch") or {}).get("note"):
            payload["watch_note"] = cfg["watch"]["note"]
    sc = build_scorecard(candidates, now)
    if sc:
        payload["scorecard"] = sc
    fees = build_fees_ytd(cfg, fx, now)
    if fees:
        payload["fees_ytd"] = fees
    if tax_info:
        rem, tax_plan = build_tax_reminders(cfg, positions, payload.get("crypto"), tax_info, fx, now)
        if rem:
            payload["tax_reminders"] = rem
        if tax_plan:
            payload["tax_plan"] = tax_plan
    exit_alerts = []
    for name, r in [(s_, positions[s_]) for s_ in order] + [(c["coin"], c) for c in (payload.get("crypto") or [])]:
        ex = r.get("exit") or {}
        if ex.get("status"):
            exit_alerts.append({"name": name, "status": ex["status"],
                                "level": ex["take_profit"] if ex["status"] in ("tp", "near_tp") else ex["stop"],
                                "currency": ex.get("currency", "USD")})
    if exit_alerts:
        payload["exit_alerts"] = exit_alerts
    stock_notes = build_stock_notes(cfg, positions, order, payload.get("crypto"), payload.get("wealth"),
                                    payload.get("scorecard"), candidates, notes.get("briefs"),
                                    prev.get("stock_notes"), now)
    payload["stock_notes"] = stock_notes
    # ensure_ascii=True: Turkce karakterler \uXXXX olarak yazilir. data.js harici bir
    # script olarak yuklendigi icin sunucunun charset bilgisine bagli kalmasin --
    # aksi halde "Eylul" yerine "EylÃ¼l" gibi bozuk karakterler cikiyor.
    with open(a.out, "w", encoding="ascii") as f:
        f.write("window.__PORTFOLIO__ = ")
        json.dump(payload, f, ensure_ascii=True, separators=(",", ":"))
        f.write(";\n")

    # ---- brief: model data.js'i okumadan ne oldugunu anlasin
    news_needed = sorted({x["sym"] for x in alerts if x["kind"] in ("move", "rsi")})
    L = ["MOD: %s   URETIM: %s (UTC)" % (a.mode, payload["generated_at"]),
         "TOPLAM: $%.2f (maliyet $%.2f, getiri %+.2f%%)   GUN: %+.2f%%"
         % (tot_val, tot_cost, summary["total_gain_pct"], summary["day_change_pct"]),
         "EN IYI: %s   EN ZAYIF: %s   TAZE VERI: %d/%d"
         % (summary["best"], summary["worst"], fresh_count, len(cfg["positions"])),
         "POZISYONLAR (sembol fiyat gun% getiri% rsi trend):"]
    for s in order:
        p = positions[s]
        L.append("  %-5s %9.2f %+7.2f%% %+9.2f%% rsi=%-6s %-9s%s"
                 % (s, p["current_price"], p.get("day_change_pct") or 0,
                    p.get("gain_loss_pct") or 0, p.get("rsi14"), p.get("trend"),
                    " [STALE]" if p.get("stale") else ""))
    L.append("UYARILAR: " + ("; ".join("%s %s" % (x["sym"], x["detail"]) for x in alerts) or "yok"))
    if since:
        L.append("SON RAPORDAN BU YANA (%s): portfoy %s%% ($%s -> $%s); hareket edenler: %s"
                 % (since.get("prev_label"), since.get("portfolio_pct"),
                    since.get("portfolio_from"), since.get("portfolio_to"),
                    ", ".join("%s %+.2f%%" % (r["sym"], r["pct"]) for r in since["per_symbol"]) or "yok"))
    lbl = {"tp": "kar al seviyesine ulasti", "stop": "zarar kes seviyesine indi",
           "near_tp": "kar al seviyesine %5'ten yakin", "near_stop": "zarar kes seviyesine %5'ten yakin"}
    L.append("KURAL: " + ("; ".join("%s %s (%s %s)" % (x["name"], lbl[x["status"]], x["level"],
                                                          "EUR" if x["currency"] == "EUR" else "$")
                                    for x in exit_alerts) or "yok"))
    ci = payload.get("cash")
    if ci:
        L.append("NAKIT: %.1f%% (hedef %d%%, acik %.0f EUR); plan '%s' +%.0f EUR (kar %+.0f EUR); REJIM: %d/%d ayi sinyali, onerilen bant %%%d"
                 % (ci["cash_pct"], ci["min_cash_pct"], ci["gap_eur"], ci["plan_name"], ci["plan_eur"],
                    ci["plan_gain_eur"], ci["bear_count"], ci["signal_count"], ci["band_pct"]))
    ci_ = payload.get("cash") or {}
    if ci_.get("eth"):
        L.append("ETH ORANI: %%%s (senin sinirin %%%s)%s" % (ci_["eth"]["pct"], ci_["eth"]["limit_pct"],
                 " -- sinir asildi, fazlalik %.0f EUR" % ci_["eth"]["excess_eur"] if ci_["eth"]["excess_eur"] else ""))
    if payload.get("watch"):
        L.append("TAKVIM: " + "; ".join("%s %s (%s g)" % (e["date"][5:], e["title"][:32], e["days_left"])
                                       for e in payload["watch"][:3]))
    L.append("VERGI: " + ("; ".join(x["short"] for x in payload.get("tax_reminders") or [] if x.get("notify"))
                          or "yok"))
    L.append("HABER NOTU YENILENMESI GEREKEN: " + (", ".join(news_needed) or "yok"))
    rk = stock_notes.get("risk") or {}
    L.append("HISSE NOTU ADAYLARI (karne ilk %d): %s   RISK BUTCESI: kullanilan %s / %s EUR, bos %s"
             % (NOTE_CANDIDATES, ", ".join(stock_notes.get("candidate_symbols") or []) or "yok",
                rk.get("used_eur"), rk.get("budget_eur"), rk.get("free_eur")))
    old = [i["symbol"] for i in stock_notes["items"] if not i.get("brief") or i["brief"].get("stale")]
    L.append("HISSE NOTU EKSIK/ESKI: " + (", ".join(old) or "yok"))
    if payload["failed"]:
        L.append("TAZE VERI ALINAMAYAN: " + ", ".join(payload["failed"]))
    brief = "\n".join(L)
    print(brief)
    if a.brief:
        open(a.brief, "w", encoding="utf-8").write(brief + "\n")


if __name__ == "__main__":
    main()
