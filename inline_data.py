#!/usr/bin/env python3
"""data.js icerigini index.html'in ICINE gomer (yan dosya bagimliligini kaldirir).

Neden: artifact'in mobil goruntuleyicisi <script src="data.js"> gibi goreli yan
dosya referanslarini cozemiyor; sayfa telefonda "Veri dosyasi (data.js) yuklenemedi."
diyerek bos aciliyor. Vedat sayfayi agirlikla telefondan aciyor, yani bu kozmetik
degil islevsel bir ariza.

Token maliyeti yok: HTML'i model degil bu script yazar, byte'lar bagbama girmez.

Idempotent: ilk kosuda <script src="data.js"></script> satirini isaretli bir inline
blokla degistirir; sonraki kosularda ayni blogun icerigini tazeler. Bu yuzden rutin
bir onceki kosunun urettigi index.html'i indirip tekrar calistirdiginda sisme olmaz.

Kullanim:  python3 inline_data.py index.html data.js
"""
import re
import sys

BASLA = "<!--PORTFOLIO-DATA-->"
BITIR = "<!--/PORTFOLIO-DATA-->"
HARICI = '<script src="data.js"></script>'


def blok(data_js):
    # </script> dizisi veri icinde gecerse script etiketi erken kapanir; kacir.
    guvenli = data_js.replace("</script", "<\\/script")
    return "%s<script>%s</script>%s" % (BASLA, guvenli, BITIR)


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("kullanim: inline_data.py <index.html> <data.js>\n")
        return 2

    html_yol, data_yol = sys.argv[1], sys.argv[2]

    with open(html_yol, encoding="utf-8") as f:
        html = f.read()
    with open(data_yol, encoding="utf-8") as f:
        data_js = f.read().strip()

    if "window.__PORTFOLIO__" not in data_js:
        sys.stderr.write("HATA: %s icinde window.__PORTFOLIO__ yok; yazilmadi.\n" % data_yol)
        return 1

    yeni = blok(data_js)
    desen = re.compile(re.escape(BASLA) + ".*?" + re.escape(BITIR), re.DOTALL)

    if desen.search(html):
        html_yeni = desen.sub(lambda _: yeni, html, count=1)
        nasil = "mevcut inline blok tazelendi"
    elif HARICI in html:
        html_yeni = html.replace(HARICI, yeni, 1)
        nasil = "harici <script src> inline bloga cevrildi"
    else:
        sys.stderr.write(
            "HATA: %s icinde ne inline blok ne de %s bulundu; yazilmadi.\n"
            % (html_yol, HARICI))
        return 1

    # Guvenlik agi: veri gercekten girmis mi, sayfa kisalmis mi?
    if "window.__PORTFOLIO__" not in html_yeni:
        sys.stderr.write("HATA: gomme sonrasi veri bulunamadi; yazilmadi.\n")
        return 1
    if desen.search(html_yeni) is None:
        sys.stderr.write("HATA: isaretli blok olusmadi; yazilmadi.\n")
        return 1

    with open(html_yol, "w", encoding="utf-8") as f:
        f.write(html_yeni)

    print("VERI GOMULDU -> %s" % html_yol)
    print("  yontem      : %s" % nasil)
    print("  data.js     : %d bayt" % len(data_js))
    print("  index.html  : %d -> %d bayt" % (len(html), len(html_yeni)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
