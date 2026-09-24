# portfoy-veri — Portföy Takibi için fiyat kanalı

Bu repo **tek bir iş** yapar: ABD borsası fiyatlarını çekip `quotes.json` dosyasına yazar.
Portföy sayfasını (artifact) güncelleyen Claude bulut rutini bu dosyayı okur.

## Neden böyle bir ara katman var?

Claude bulut rutinlerinin çalıştığı sandbox'tan **bütün finans veri host'ları engelli**
(egress proxy 403 veriyor — Yahoo, Nasdaq, Stooq, Twelve Data, AlphaVantage; `WebFetch` aracı da dahil).
GitHub Actions çalıştırıcısında böyle bir engel yok ve `raw.githubusercontent.com`
sandbox'tan **erişilebilir**. Zincir şöyle kuruluyor:

```
GitHub Actions (cron)          Claude bulut rutini              Artifact (özel)
  fetch_quotes.py      →   quotes.json'u okur, build.py ile   →   Portföy Takibi
  Yahoo'dan fiyat          hesaplar, haber notunu ekler            sayfası güncellenir
  quotes.json commit       artifact'i yayınlar
```

## Gizlilik

Bu repoda **portföy bilgisi yoktur**: adet, maliyet, kâr/zarar hiç bulunmaz.
`tickers.json` yalnızca fiyatı çekilecek sembolleri listeler; adet ve ortalama maliyet
özel artifact'in içindeki `positions.json` dosyasında kalır.

## Dosyalar

| Dosya | Ne yapar |
|---|---|
| `fetch_quotes.py` | Yahoo Finance'ten fiyat + 6 aylık kapanış serisi + 52 hafta aralığı çeker |
| `tickers.json` | Semboller (13 pozisyon + 35 aday hisse + Radar'daki halka açık şirketler). Yeni hisse eklenince burası da güncellenir |
| `quotes.json` | Çıktı — her koşuda Actions tarafından güncellenir |
| `.github/workflows/quotes.yml` | Zamanlama |

## Zamanlama (UTC)

| Cron | Mod | Ne çekilir |
|---|---|---|
| `5 6 * * 1-5` | sabah | Pozisyon fiyatları |
| `5 21 * * 1-5` | aksam | Pozisyon fiyatları (ABD kapanışı sonrası) |
| `55 16 * * 0` | pazar | Pozisyonlar + 35 aday hissenin tamamı |

Claude rutinleri bunlardan ~30 dakika sonra çalışır (Actions zamanlanmış işleri
yoğun saatlerde birkaç dakika gecikebilir).

## Kurulum

1. Bu dosyaları yeni bir repoya yükle (özel repo olabilir; rutin `raw` erişimi için
   repo **public** olmalı ya da Claude hesabına GitHub bağlantısı kurulmalı).
2. Actions sekmesinde workflow'u etkinleştir.
3. `Actions → Portföy fiyat verisi → Run workflow` ile elle bir kez çalıştır,
   `quotes.json`'un güncellendiğini gör.

## Elle çalıştırma

```bash
python3 fetch_quotes.py --mode aksam     # veya sabah / pazar
```

Ek paket gerekmez: Python standart kütüphanesi + `curl`.

## Bakım notları

- **GitHub, 60 gün hiç işlem görmeyen repolarda zamanlanmış workflow'ları durdurur.**
  Ayda bir commit atmak veya Actions sekmesinden yeniden etkinleştirmek yeterli.
- Yahoo, tam Chrome user-agent dizesine 429 döndürüyor; script kısa `Mozilla/5.0`
  kullanıyor ve kısıtlanma halinde UA değiştirip bekleyerek tekrar deniyor.
- Sembollerin yarısından fazlası çekilemezse script exit code 1 ile durur ve
  `quotes.json` bozulmaz — eski dosya olduğu gibi kalır.
