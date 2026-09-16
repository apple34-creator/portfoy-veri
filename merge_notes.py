#!/usr/bin/env python3
"""notes.json arsivine, bulut rutininin yazdigi delta notlari birlestirir.

Neden ayri bir script: rutin prompt'u notes-yeni.json'i dogrudan notes.json
olarak yayinlarsa arsiv (13 pozisyonun eski notlari + makro ozet + takvim +
aday hisse analist notlari) silinir. build.py --merge-notes yalnizca data.js
yazar, notes.json'a dokunmaz. Bu bosluk burada kapatiliyor.

Kullanim:  python3 merge_notes.py notes.json notes-yeni.json
"""
import json
import sys


def deep_merge_symbols(base, delta):
    """Sembol -> alan sozlugu; yalnizca delta'da gelen alanlari ezer."""
    for sym, fields in (delta or {}).items():
        if not isinstance(fields, dict):
            continue
        base.setdefault(sym, {}).update(fields)


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("kullanim: merge_notes.py <arsiv notes.json> <delta notes-yeni.json>\n")
        return 2

    arsiv_yol, delta_yol = sys.argv[1], sys.argv[2]

    with open(arsiv_yol, encoding="utf-8") as f:
        arsiv = json.load(f)
    with open(delta_yol, encoding="utf-8") as f:
        delta = json.load(f)

    if not isinstance(arsiv, dict) or not isinstance(delta, dict):
        sys.stderr.write("HATA: iki dosya da JSON nesnesi olmali.\n")
        return 1

    before = len(arsiv.get("positions") or {})

    arsiv.setdefault("positions", {})
    deep_merge_symbols(arsiv["positions"], delta.get("positions"))

    if delta.get("candidates"):
        arsiv.setdefault("candidates", {})
        deep_merge_symbols(arsiv["candidates"], delta["candidates"])

    # Istihbarat: alerts/calendar/radar tumden degisir (rutin gecerlileri tasir),
    # companies sembol bazinda ezilir -- build.py merge_intel ile ayni kural.
    if delta.get("intel"):
        intel = arsiv.setdefault("intel", {})
        for key in ("alerts", "calendar", "radar", "updated_at"):
            if key in delta["intel"]:
                intel[key] = delta["intel"][key]
        if delta["intel"].get("companies"):
            intel.setdefault("companies", {}).update(delta["intel"]["companies"])

    for key in ("macro_summary", "macro_calendar"):
        if delta.get(key):
            arsiv[key] = delta[key]

    # since_last_text gecici bir alan: data.js'e build.py isliyor, arsivde yeri yok.

    after = len(arsiv["positions"])
    if after < before:
        sys.stderr.write("HATA: pozisyon sayisi azaldi (%d -> %d); yazilmadi.\n" % (before, after))
        return 1

    with open(arsiv_yol, "w", encoding="utf-8") as f:
        json.dump(arsiv, f, ensure_ascii=False, indent=1)
        f.write("\n")

    print("ARSIV BIRLESTIRILDI -> %s" % arsiv_yol)
    print("  guncellenen pozisyon notu: %s"
          % (", ".join((delta.get("positions") or {}).keys()) or "yok"))
    print("  guncellenen aday notu    : %s"
          % (", ".join((delta.get("candidates") or {}).keys()) or "yok"))
    print("  makro ozet: %s | takvim: %s"
          % ("evet" if delta.get("macro_summary") else "hayir",
             "evet" if delta.get("macro_calendar") else "hayir"))
    print("  istihbarat: %s" % ("evet" if delta.get("intel") else "hayir"))
    print("  toplam pozisyon notu: %d" % after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
