#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Portfoy Takibi -- fiyat cekici (GitHub Actions icinde calisir).

Neden bu dosya var: Claude bulut rutinlerinin calistigi sandbox'tan butun finans
veri hostlari egress proxy tarafindan 403 ile engelli. GitHub Actions
calistiricisinda boyle bir engel yok. Bu script fiyatlari cekip quotes.json
olarak repoya yazar; Claude rutini de o dosyayi raw.githubusercontent.com
uzerinden okuyup (o adres sandbox'tan erisilebilir) hesaplari yapar.

Girdi : tickers.json  (semboller -- pozisyon adedi/maliyeti ICERMEZ)
        quotes.json   (varsa; pazar disi kosularda aday listesi devralinir)
Cikti : quotes.json   {asof, mode, quotes, series, candidates}

Kullanim: python3 fetch_quotes.py --mode aksam
Sadece Python standart kutuphanesi + curl. Ek paket gerekmez.
"""

import argparse, json, os, shutil, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

# Yahoo, tam Chrome user-agent dizesini 429 ile blokluyor; kisa UA sorunsuz.
UA_LIST = ["Mozilla/5.0", "curl/8.7.1", "Mozilla/5.0 (X11; Linux x86_64)"]
SPARK = ("https://query1.finance.yahoo.com/v7/finance/spark"
         "?symbols={syms}&range={rng}&interval={iv}")
CHUNK, CHUNK_SLEEP, BACKOFF = 18, 8.0, [10, 25, 45]


class Throttled(Exception):
    pass


def http_get(url, timeout=30, ua_index=0):
    body, ua = None, UA_LIST[ua_index % len(UA_LIST)]
    curl = shutil.which("curl")
    if curl:
        p = subprocess.run([curl, "-sS", "--max-time", str(timeout),
                            "-H", "User-Agent: " + ua, url], capture_output=True)
        if p.returncode == 0 and p.stdout.strip():
            body = p.stdout
    if body is None:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise Throttled("429")
            raise
    if body[:200].lstrip()[:1] not in (b"{", b"["):
        raise Throttled("JSON degil: %s" % body[:80].decode("utf-8", "replace"))
    return body


def fetch_spark(symbols, rng="1y", iv="1d", attempts=4):
    out = {}
    for i in range(0, len(symbols), CHUNK):
        group = symbols[i:i + CHUNK]
        url = SPARK.format(syms=",".join(group), rng=rng, iv=iv)
        for att in range(attempts):
            try:
                raw = json.loads(http_get(url, ua_index=att))
                for res in (raw.get("spark") or {}).get("result") or []:
                    sym = res.get("symbol")
                    resp = (res.get("response") or [{}])[0]
                    q = ((resp.get("indicators") or {}).get("quote") or [{}])[0]
                    ts, cl = [], []
                    for t, c in zip(resp.get("timestamp") or [], q.get("close") or []):
                        if c is None:
                            continue
                        ts.append(int(t))
                        cl.append(float(c))
                    if sym and cl:
                        out[sym] = {"ts": ts, "closes": cl, "meta": resp.get("meta") or {}}
                break
            except Throttled as e:
                wait = BACKOFF[min(att, len(BACKOFF) - 1)]
                sys.stderr.write("! kisitlandi (%s): %s -- %ds bekleniyor\n" % (group[0], e, wait))
                if att + 1 < attempts:
                    time.sleep(wait)
            except Exception as e:
                sys.stderr.write("! spark hatasi (%s): %s\n" % (group[0], e))
                if att + 1 < attempts:
                    time.sleep(5)
        time.sleep(CHUNK_SLEEP)
    missing = [s for s in symbols if s not in out]
    if missing:
        sys.stderr.write("! gelmeyen semboller: %s\n" % ", ".join(missing))
    return out


def slice_6mo(ts, closes, days=182):
    cutoff = int(time.time()) - days * 86400
    for i, t in enumerate(ts):
        if t >= cutoff:
            return ts[i:], closes[i:]
    return ts[-126:], closes[-126:]


def pct(a, b):
    return None if not b else round((a - b) / b * 100, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["sabah", "aksam", "pazar"])
    ap.add_argument("--tickers", default="tickers.json")
    ap.add_argument("--out", default="quotes.json")
    a = ap.parse_args()

    cfg = json.load(open(a.tickers, encoding="utf-8"))
    prev = {}
    if os.path.exists(a.out):
        try:
            prev = json.load(open(a.out, encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    syms = cfg["positions"]
    bulk = fetch_spark(syms, "1y", "1d")
    if len(bulk) < len(syms) / 2:
        sys.stderr.write("HATA: pozisyonlarin yarisindan fazlasi cekilemedi (%d/%d).\n"
                         % (len(bulk), len(syms)))
        sys.exit(1)

    quotes, series = {}, {}
    for s in syms:
        d = bulk.get(s)
        if not d:
            old = (prev.get("quotes") or {}).get(s)
            if old:
                quotes[s] = dict(old, stale=True)
            continue
        closes, meta = list(d["closes"]), d["meta"]
        price = float(meta.get("regularMarketPrice") or closes[-1])
        closes[-1] = price
        ts6, cl6 = slice_6mo(d["ts"], closes)
        quotes[s] = {
            "price": round(price, 4),
            "day_change_pct": round(float(meta.get("regularMarketChangePercent") or 0), 2),
            "week52_high": round(float(meta.get("fiftyTwoWeekHigh") or max(closes)), 2),
            "week52_low": round(float(meta.get("fiftyTwoWeekLow") or min(closes)), 2),
            "chg_1y_pct": pct(price, closes[0]) if len(closes) > 200 else None,
        }
        series[s] = {"ts": ts6, "closes": [round(c, 2) for c in cl6]}

    # Aday hisseler yalnizca pazar kosusunda yenilenir; digerlerinde devralinir.
    if a.mode == "pazar" and cfg.get("candidates"):
        cand_syms = [c["symbol"] for c in cfg["candidates"]]
        cb = fetch_spark(cand_syms, "1y", "1d")
        candidates = []
        for c in cfg["candidates"]:
            d = cb.get(c["symbol"])
            if not d:
                old = next((x for x in (prev.get("candidates") or [])
                            if x.get("symbol") == c["symbol"]), None)
                candidates.append(old or {"symbol": c["symbol"], "sector": c.get("sector", "-")})
                continue
            closes, meta = list(d["closes"]), d["meta"]
            price = float(meta.get("regularMarketPrice") or closes[-1])
            closes[-1] = price
            hi = float(meta.get("fiftyTwoWeekHigh") or max(closes))
            lo = float(meta.get("fiftyTwoWeekLow") or min(closes))
            _, cl6 = slice_6mo(d["ts"], closes)
            candidates.append({
                "symbol": c["symbol"], "sector": c.get("sector", "-"),
                "current_price": round(price, 2),
                "chg_1y_pct": pct(price, closes[0]),
                "chg_6mo_pct": pct(price, cl6[0]) if cl6 else None,
                "week52_high": round(hi, 2), "week52_low": round(lo, 2),
                "pos_in_52w_range_pct": round((price - lo) / ((hi - lo) or 1) * 100, 1),
            })
    else:
        candidates = prev.get("candidates") or []

    # Doviz kuru: Alman vergisi kari EURO ile hesaplar (alis gunu kuru / satis gunu kuru).
    # 5 yillik gunluk seri saklanir ki eski alimlarin kuru da bulunabilsin.
    # Cekilemezse onceki deger devralinir; hisse fiyatlari bundan etkilenmez.
    fx = dict(prev.get("fx") or {})
    for fsym in cfg.get("fx") or []:
        d = fetch_spark([fsym], "5y", "1d").get(fsym)
        if not d:
            continue
        key = fsym.replace("=X", "")
        rate = float(d["meta"].get("regularMarketPrice") or d["closes"][-1])
        fx[key] = {
            "rate": round(rate, 5),
            "ts": d["ts"],
            "closes": [round(c, 5) for c in d["closes"]],
        }

    # Kripto (ornek: BTC-EUR): fiyat dogrudan euro. Adet/maliyet ASLA burada tutulmaz.
    crypto = dict(prev.get("crypto") or {})
    csyms = cfg.get("crypto") or []
    if csyms:
        cb = fetch_spark(csyms, "1y", "1d")
        for c in csyms:
            d = cb.get(c)
            if not d:
                if c in crypto:
                    crypto[c] = dict(crypto[c], stale=True)
                continue
            closes, meta = d["closes"], d["meta"]
            price = float(meta.get("regularMarketPrice") or closes[-1])
            crypto[c] = {
                "price": round(price, 6),
                "day_change_pct": round(float(meta.get("regularMarketChangePercent") or 0), 2),
                "week52_high": round(max(closes), 6),
                "week52_low": round(min(closes), 6),
                "chg_1y_pct": pct(price, closes[0]) if len(closes) > 200 else None,
            }

    # Endeks kiyasi (Faz 13d): SPY (S&P 500), URTH (MSCI World). Alis gunlerindeki fiyat
    # lazim oldugu icin kur gibi 5 yillik gunluk seri saklanir. Cekilemezse onceki devralinir.
    benchmarks = dict(prev.get("benchmarks") or {})
    bsyms = cfg.get("benchmarks") or []
    if bsyms:
        bb = fetch_spark(bsyms, "5y", "1d")
        for b in bsyms:
            d = bb.get(b)
            if not d:
                if b in benchmarks:
                    benchmarks[b] = dict(benchmarks[b], stale=True)
                continue
            benchmarks[b] = {
                "price": round(float(d["meta"].get("regularMarketPrice") or d["closes"][-1]), 4),
                "ts": d["ts"],
                "closes": [round(c, 4) for c in d["closes"]],
            }

    doc = {
        "asof": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "mode": a.mode,
        "source": "Yahoo Finance spark (GitHub Actions)",
        "quotes": quotes, "series": series, "candidates": candidates,
        "fx": fx, "crypto": crypto, "benchmarks": benchmarks,
    }
    with open(a.out, "w", encoding="ascii") as f:
        json.dump(doc, f, ensure_ascii=True, separators=(",", ":"))
        f.write("\n")

    print("%s yazildi | mod=%s | %d sembol | %d aday | kur: %s | kripto: %d | %s"
          % (a.out, a.mode, len(quotes), len(candidates),
             ", ".join("%s=%s" % (k, v["rate"]) for k, v in fx.items()) or "yok",
             len(crypto), doc["asof"]))
    for s in syms:
        q = quotes.get(s)
        if q:
            print("  %-5s %9.2f  gun %+6.2f%%%s"
                  % (s, q["price"], q["day_change_pct"] or 0, "  [STALE]" if q.get("stale") else ""))


if __name__ == "__main__":
    main()
