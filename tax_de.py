#!/usr/bin/env python3
"""Almanya vergi hesaplari -- Portfoy Takibi (Faz 11).

Saf hesap modulu: ag erisimi yok, kisisel veri yok. build.py cagirir.
Butun tutarlar EURO. Alman vergisi kari euro ile olcer: alis tutari alis gunu
kuruyla, satis tutari satis gunu kuruyla euroya cevrilir (kur farki kara dahildir).

Dayanaklar (21 Eylul 2026'da WebSearch ile dogrulandi):
- Hisse: Abgeltungsteuer %25 + Solidaritatszuschlag %5,5 (vergi uzerinden) = %26,375.
  Kilise vergisi varsa ozel formul (kist_rate ile).
- Sparer-Pauschbetrag: bekar 1.000 EUR, evli 2.000 EUR / yil. Kullanilmayan kisim yanar.
- Hisse zararlari yalnizca hisse karlariyla mahsup edilir (Aktienverlustverrechnungstopf,
  par. 20 Abs. 6 S. 4 EStG). BVerfG 2 BvL 3/21 karari 2026'da bekleniyor -- degisirse
  STOCK_LOSS_ISOLATED False yapilir.
- Ayni gun sat + geri al: BFH IX R 5/16 (2017) -- Gestaltungsmissbrauch degil.
- Kripto (par. 23 EStG): 1 yildan uzun tutulursa satis vergisiz. Kisa vadeli karlarin
  yillik TOPLAMI 1.000 EUR'nun altindaysa vergisiz; ulasir ya da asarsa TAMAMI kisisel
  gelir vergisi oraniyla vergilenir (Freigrenze, Freibetrag degil).
- Taslak (8 Eylul 2026, Referentenentwurf): 31.12.2026 sonrasi alinan kriptoya
  Abgeltungsteuer. KESINLESMEDI -- sadece bilgi notu olarak kullanilir.
"""
from datetime import date, timedelta

STOCK_BASE_RATE = 0.25
SOLI_RATE = 0.055
STOCK_LOSS_ISOLATED = True       # hisse zarari yalnizca hisse kariyla mahsup
CRYPTO_FREIGRENZE = 1000.0       # kisa vadeli kripto kari: < bu tutar ise vergisiz
CRYPTO_REFORM_CUTOFF = date(2026, 12, 31)   # taslak: bu tarihten SONRA alinanlar etkilenebilir

DEFAULT_PROFILE = {
    "allowance": 1000.0,          # Sparer-Pauschbetrag (bekar)
    "kist_rate": 0.0,             # kilise vergisi: 0 / 0.08 / 0.09
    "income_tax_rate": 0.30,      # kripto kisa vade icin kisisel oran (tahmin, ayarlanabilir)
    "income_tax_rate_is_estimate": True,
    "commission_pct": 0.0012,     # Revolut: ucretsiz kotayi asan emir basina %0,12
    "spread_pct": 0.001,          # alis-satis farki tahmini
    "fx_fee_pct": 0.0,            # sat-geri al USD icinde kalir; ceviri yok
    "broker": "Revolut",
    "withholds_tax": False,       # Revolut Almanya'da vergi kesmiyor -> kenara ayir
}


def profile(p=None):
    out = dict(DEFAULT_PROFILE)
    out.update(p or {})
    return out


def stock_rate(prof):
    """Hisse kazancinda efektif oran (Abgeltungsteuer + Soli + varsa KiSt).

    KiSt yoksa: 0,25 * 1,055 = 0,26375.
    KiSt varsa (par. 32d EStG): e = kazanc / (4 + k); vergi = e + e*0,055 + e*k.
    """
    k = float(prof.get("kist_rate") or 0)
    if not k:
        return STOCK_BASE_RATE * (1 + SOLI_RATE)
    base = 1.0 / (4 + k)
    return base * (1 + SOLI_RATE + k)


# ------------------------------------------------------------------ kur
def fx_on(day, fx):
    """day (date) icin USD->EUR kuru (EURUSD, yani 1 EUR = x USD).

    fx: {"rate": guncel, "ts": [...], "closes": [...]}. O gun kapanis yoksa (hafta sonu,
    tatil) bir onceki is gununun kurunu alir. Seri o tarihten once basliyorsa ilk degeri,
    seri yoksa guncel kuru dondurur; ikinci donus degeri 'kesin mi' bilgisidir.
    """
    if not fx:
        return None, False
    ts, cl = fx.get("ts") or [], fx.get("closes") or []
    if not ts:
        return fx.get("rate"), False
    target = int((day - date(1970, 1, 1)).total_seconds()) + 86399
    best = None
    for t, c in zip(ts, cl):
        if t <= target:
            best = c
        else:
            break
    if best is None:
        return cl[0], False
    return best, True


def usd_to_eur(usd, eurusd):
    return usd / eurusd if eurusd else None


# ------------------------------------------------------------------ FIFO
def fifo_consume(lots, shares):
    """En eski alimdan baslayarak `shares` adet tuketir.

    lots: [{"date": "YYYY-MM-DD", "shares": x, "price_usd": p}] (tarih sirali olmasa da siralanir)
    Doner: (tuketilen parcalar [(lot, adet)], kalan lot listesi).
    """
    rest = sorted((dict(l) for l in lots), key=lambda l: l["date"])
    used, need = [], float(shares)
    while need > 1e-9 and rest:
        l = rest[0]
        take = min(need, float(l["shares"]))
        used.append((l, take))
        l["shares"] = float(l["shares"]) - take
        need -= take
        if l["shares"] <= 1e-9:
            rest.pop(0)
    return used, rest


def gain_eur_for_sale(lots, shares, price_usd_now, fx, today):
    """`shares` adet bugun satilsa euro cinsinden gerceklesen kar (FIFO, alis gunu kuru).

    Doner: (kar_eur, kesin_mi). Lot yoksa None.
    """
    if not lots:
        return None, False
    used, _ = fifo_consume(lots, shares)
    sell_rate, ok_sell = fx_on(today, fx)
    if not sell_rate:
        return None, False
    exact = ok_sell
    gain = 0.0
    for lot, n in used:
        buy_rate, ok = fx_on(date.fromisoformat(lot["date"]), fx)
        exact = exact and ok
        gain += n * (price_usd_now / sell_rate - float(lot["price_usd"]) / buy_rate)
    return gain, exact


def gain_eur_per_share_marginal(lots, price_usd_now, fx, today):
    """FIFO'da siradaki (en eski) payin satis kari -- sat-geri al icin kullanilir."""
    if not lots:
        return None
    n = min(1.0, sum(float(l["shares"]) for l in lots))
    if n <= 0:
        return None
    g, _ = gain_eur_for_sale(lots, n, price_usd_now, fx, today)
    return None if g is None else g / n


# ------------------------------------------------------------------ yillik hisse vergisi
def annual_stock_tax(stock_gains, stock_losses, other_income=0.0, other_losses=0.0,
                     loss_carryforward_stock=0.0, prof=None):
    """Bir yilin kapital kazanci vergisi (hisse + diger).

    Siralama: hisse zarari once hisse karindan dusulur (ayri havuz); genel zararlar
    butun pozitif gelirden dusulur; kalan toplamdan yillik hak dusulur; kalan vergilenir.
    Hisse havuzu eksiye duserse zarar sonraki yila devreder (diger gelirle mahsup edilmez).
    """
    prof = profile(prof)
    stock_net = stock_gains - stock_losses - loss_carryforward_stock
    if STOCK_LOSS_ISOLATED:
        stock_pos = max(stock_net, 0.0)
        carry = max(-stock_net, 0.0)
    else:
        stock_pos, carry = stock_net, 0.0
    total = stock_pos + other_income - other_losses
    total = max(total, 0.0)
    allowance = float(prof["allowance"])
    used_allowance = min(allowance, total)
    taxable = total - used_allowance
    tax = taxable * stock_rate(prof)
    return {
        "taxable": round(taxable, 2),
        "tax": round(tax, 2),
        "allowance_used": round(used_allowance, 2),
        "allowance_left": round(allowance - used_allowance, 2),
        "stock_loss_carryforward": round(carry, 2),
        "rate": stock_rate(prof),
    }


# ------------------------------------------------------------------ arac 1: yillik hakki kullan
def harvest_allowance(candidates, allowance_left, prof=None):
    """Yillik vergisiz hakki sat + geri al ile doldurma plani.

    candidates: [{"symbol", "shares", "price_usd", "gain_per_share_eur", "eurusd"}]
    Pozisyon ayni kalir (satilan adet hemen geri alinir). Sadece karli pozisyonlar.
    Pay basina kari en yuksek olandan baslar (daha az adet = daha az ucret).
    Her adim yalnizca NET fayda (kurtarilan gelecek vergi - ucret) > 0 ise onerilir.
    Doner: [{"symbol", "shares", "gain_eur", "tax_saved_eur", "fee_eur", "net_benefit_eur"}]
    """
    prof = profile(prof)
    rate = stock_rate(prof)
    round_trip = 2 * float(prof["commission_pct"]) + float(prof["spread_pct"]) + float(prof["fx_fee_pct"])
    left = float(allowance_left)
    plan = []
    for c in sorted(candidates, key=lambda c: -(c.get("gain_per_share_eur") or 0)):
        gps = c.get("gain_per_share_eur") or 0
        if left <= 1 or gps <= 0:
            continue
        n = min(float(c["shares"]), left / gps)
        n = int(n * 100) / 100.0          # 0,01 adet hassasiyet, asagi yuvarla (hakki asma)
        if n <= 0:
            continue
        gain = n * gps
        value_eur = n * c["price_usd"] / c["eurusd"]
        fee = value_eur * round_trip
        saved = gain * rate
        if saved - fee <= 0:
            continue
        plan.append({
            "symbol": c["symbol"], "shares": n,
            "gain_eur": round(gain, 2), "tax_saved_eur": round(saved, 2),
            "fee_eur": round(fee, 2), "net_benefit_eur": round(saved - fee, 2),
        })
        left -= gain
    return plan


# ------------------------------------------------------------------ arac 2: zarar hasadi
def harvest_losses(candidates, taxable_stock_gain_ytd, prof=None):
    """Yil icinde vergiye tabi hisse kari varsa, zarardaki pozisyonlardan sat + geri al.

    taxable_stock_gain_ytd: yillik hak dusuldukten sonra hala vergilenecek hisse kari.
    Bu 0 ise hic oneri yok (zarari gerceklestirmek bu yil bir sey kazandirmaz;
    devreden zarar gelecekte ise yarar ama o karar kullaniciya birakilir).
    candidates: [{"symbol", "shares", "price_usd", "loss_per_share_eur" (pozitif), "eurusd"}]
    """
    prof = profile(prof)
    rate = stock_rate(prof)
    round_trip = 2 * float(prof["commission_pct"]) + float(prof["spread_pct"]) + float(prof["fx_fee_pct"])
    need = float(taxable_stock_gain_ytd)
    plan = []
    if need <= 0:
        return plan
    for c in sorted(candidates, key=lambda c: -(c.get("loss_per_share_eur") or 0)):
        lps = c.get("loss_per_share_eur") or 0
        if need <= 1 or lps <= 0:
            continue
        n = min(float(c["shares"]), need / lps)
        n = int(n * 100) / 100.0
        if n <= 0:
            continue
        loss = n * lps
        fee = n * c["price_usd"] / c["eurusd"] * round_trip
        saved = loss * rate
        if saved - fee <= 0:
            continue
        plan.append({
            "symbol": c["symbol"], "shares": n,
            "loss_eur": round(loss, 2), "tax_saved_eur": round(saved, 2),
            "fee_eur": round(fee, 2), "net_benefit_eur": round(saved - fee, 2),
        })
        need -= loss
    return plan


# ------------------------------------------------------------------ arac 3: kripto
def crypto_taxfree_date(buy_day):
    """1 yildan UZUN tutma: alimin bir yil sonrasinin ertesi gunu vergisiz."""
    try:
        anniv = buy_day.replace(year=buy_day.year + 1)
    except ValueError:                     # 29 Subat
        anniv = buy_day.replace(year=buy_day.year + 1, day=28)
    return anniv + timedelta(days=1)


def crypto_lot_status(lot, price_eur_now, today, prof=None):
    """Tek kripto alimi icin: kac gun sonra vergisiz, bugun satilsa kar."""
    prof = profile(prof)
    buy_day = date.fromisoformat(lot["date"])
    free_on = crypto_taxfree_date(buy_day)
    gain = float(lot["amount"]) * (price_eur_now - float(lot["price_eur"]))
    return {
        "date": lot["date"],
        "amount": float(lot["amount"]),
        "gain_eur": round(gain, 2),
        "taxfree_on": free_on.isoformat(),
        "days_to_taxfree": max((free_on - today).days, 0),
        "is_taxfree": today >= free_on,
        "reform_note": buy_day > CRYPTO_REFORM_CUTOFF,
    }


def crypto_short_term_tax(short_term_gains_total, prof=None):
    """Yilin kisa vadeli kripto kari toplami icin vergi (Freigrenze: hep-ya-hic)."""
    prof = profile(prof)
    g = float(short_term_gains_total)
    if g < CRYPTO_FREIGRENZE:
        return 0.0
    return round(g * float(prof["income_tax_rate"]), 2)


# ------------------------------------------------------------------ arac 4: satis zamanlamasi
def sale_timing(gain_eur, ytd_taxable_before, allowance_left_this_year, prof=None):
    """Kullanicinin KENDI karar verdigi bir hisse satisinda: bu yil mi, Ocak'ta mi?

    ytd_taxable_before: bu yil hak dusulmeden onceki net hisse+diger kazanc (bu satis haric).
    Gelecek yil icin baska kazanc olmadigi, hakkin tam oldugu varsayilir (acikca yazilir).
    """
    prof = profile(prof)
    rate = stock_rate(prof)
    allowance = float(prof["allowance"])
    g = float(gain_eur)
    this_year = max(0.0, ytd_taxable_before + g - allowance) - max(0.0, ytd_taxable_before - allowance)
    next_year = max(0.0, g - allowance)
    half = g / 2
    split = (max(0.0, ytd_taxable_before + half - allowance) - max(0.0, ytd_taxable_before - allowance)
             + max(0.0, half - allowance))
    return {
        "tax_this_year": round(this_year * rate, 2),
        "tax_next_year": round(next_year * rate, 2),
        "tax_split_half": round(split * rate, 2),
    }


def after_tax_gain(unrealized_gain_eur, prof=None):
    """Bugun tamami satilsa (bu yilin hakki bos varsayilarak degil, yalnizca oranla)
    elde kalacak kar -- kartlardaki ana olcu. Zararsa vergi 0."""
    prof = profile(prof)
    g = float(unrealized_gain_eur)
    if g <= 0:
        return round(g, 2), 0.0
    tax = g * stock_rate(prof)
    return round(g - tax, 2), round(tax, 2)
