#!/usr/bin/env python3
"""AES-256-GCM sifreleme yardimcisi (Portfoy Takibi).

Bicim: "v1.<base64 iv>.<base64 sifreli+etiket>" -- sayfadaki WebCrypto cozucusu ayni bicimi okur.
Anahtar: 32 rastgele bayt, base64; ortam degiskeni PORTFOY_KEY ya da --keyfile.

Kullanim:
  python3 ci/pcrypt.py enc GIRIS CIKIS      # dosyayi sifrele
  python3 ci/pcrypt.py dec GIRIS CIKIS      # sifreyi coz
"""
import argparse
import base64
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def load_key(keyfile=None):
    raw = None
    if keyfile:
        raw = open(keyfile, encoding="utf-8").read().strip()
    else:
        raw = (os.environ.get("PORTFOY_KEY") or "").strip()
    if not raw:
        sys.exit("HATA: PORTFOY_KEY yok (ortam degiskeni ya da --keyfile).")
    key = base64.b64decode(raw)
    if len(key) != 32:
        sys.exit("HATA: anahtar 32 bayt olmali (base64).")
    return key


def encrypt(data, key):
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, data, None)
    return "v1.%s.%s" % (base64.b64encode(iv).decode(), base64.b64encode(ct).decode())


def decrypt(token, key):
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise ValueError("bilinmeyen sifre bicimi")
    iv, ct = base64.b64decode(parts[1]), base64.b64decode(parts[2])
    return AESGCM(key).decrypt(iv, ct, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["enc", "dec"])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--keyfile")
    a = ap.parse_args()
    key = load_key(a.keyfile)
    if a.op == "enc":
        out = encrypt(open(a.src, "rb").read(), key)
        open(a.dst, "w", encoding="utf-8").write(out)
    else:
        out = decrypt(open(a.src, encoding="utf-8").read(), key)
        open(a.dst, "wb").write(out)


if __name__ == "__main__":
    main()
