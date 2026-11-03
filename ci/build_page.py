#!/usr/bin/env python3
"""Sifreli durumdan portfoy sayfasini uretir (GitHub Actions'ta calisir).

Girdi : state.enc (sifreli: positions.json + notes.json + onceki data.js), quotes.json
Cikti : <out>/index.html   sayfa; veri AES-GCM ile sifreli gomulu, anahtar yer tutucu
        <out>/status.json  herkese acik, hassas olmayan ozet (yuzde/sembol; tutar YOK)
        <out>/state.enc    yeni durum (sonraki kosu icin)
        <out>/state.prev.enc  bir onceki durum (sigorta)

Guvenlik: herkese acik depoda calisir -> build ciktisi (tutarlar) ASLA loga yazilmaz.
Hata olursa cikis kodu 1 ve mevcut yayin dokunulmadan kalir.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcrypt  # noqa: E402


def fail(msg):
    sys.stderr.write("HATA: %s\n" % msg)
    sys.exit(1)


def parse_data_js(text):
    return json.loads(text[text.index("=") + 1:].strip().rstrip(";"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["sabah", "aksam", "pazar"])
    ap.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--state", required=True, help="mevcut state.enc yolu")
    ap.add_argument("--out", required=True, help="cikti klasoru")
    ap.add_argument("--keyfile")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    key = pcrypt.load_key(a.keyfile)

    if not os.path.exists(a.state):
        fail("state.enc yok (ilk kurulum yapilmamis).")
    try:
        state = json.loads(pcrypt.decrypt(open(a.state, encoding="utf-8").read(), key))
    except Exception:
        fail("state.enc cozulemedi (anahtar yanlis ya da dosya bozuk).")
    for k in ("positions", "notes", "prev_js"):
        if k not in state:
            fail("state.enc eksik alan: %s" % k)

    work = tempfile.mkdtemp(prefix="portfoy-")
    try:
        cfg = state["positions"]
        json.dump(cfg, open(os.path.join(work, "positions.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        json.dump(state["notes"], open(os.path.join(work, "notes.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        open(os.path.join(work, "data.js"), "w", encoding="utf-8").write(state["prev_js"])
        for f in ("quotes.json", "build.py", "tax_de.py"):
            shutil.copy(os.path.join(root, f), os.path.join(work, f))

        env = dict(os.environ, TZ="UTC", PYTHONIOENCODING="utf-8")
        r = subprocess.run(
            [sys.executable, "build.py", "--mode", a.mode, "--quotes", "quotes.json", "--brief", "brief.txt"],
            cwd=work, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            tail = " | ".join((r.stderr or "").strip().splitlines()[-3:])
            fail("build.py basarisiz (cikis %d): %s" % (r.returncode, tail[:300]))

        data_js = open(os.path.join(work, "data.js"), encoding="utf-8").read()
        payload = parse_data_js(data_js)
        qdoc = json.load(open(os.path.join(work, "quotes.json"), encoding="utf-8"))

        # ---- dogrulama: bozuk bir yayin eskisinden kotudur
        want = [p["symbol"] for p in cfg["positions"]]
        have = list((payload.get("positions") or {}).keys())
        missing = [s for s in want if s not in have]
        if missing:
            fail("payload'da eksik pozisyon: %s" % ",".join(missing))
        total = (payload.get("summary") or {}).get("total_value")
        prev_total = ((parse_data_js(state["prev_js"]).get("summary")) or {}).get("total_value")
        if not isinstance(total, (int, float)) or total <= 0:
            fail("toplam deger gecersiz")
        if prev_total and not (0.5 < total / prev_total < 2.0):
            fail("toplam deger onceki kosuya gore asiri degisti (%.0f%%); yayin durduruldu"
                 % ((total / prev_total - 1) * 100))
        if len(json.dumps(payload)) < 50000:
            fail("payload beklenenden kucuk")

        now = datetime.now(timezone.utc)
        payload["quotes_asof_iso"] = qdoc.get("asof")
        payload["built_at_iso"] = now.isoformat(timespec="minutes")

        enc_payload = pcrypt.encrypt(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), key)

        tpl = open(os.path.join(root, "page", "template.html"), encoding="utf-8").read()
        for ph in ("__BUILD_STAMP__", "__ENC_PAYLOAD__"):
            if tpl.count(ph) != 1:
                fail("sablonda %s bir kez gecmeli" % ph)
        stamp = "built=%s mode=%s quotes=%s" % (now.strftime("%Y-%m-%dT%H:%MZ"), a.mode, qdoc.get("asof"))
        html = tpl.replace("__BUILD_STAMP__", stamp).replace("__ENC_PAYLOAD__", enc_payload)

        new_state = pcrypt.encrypt(json.dumps(
            {"v": 1, "positions": cfg, "notes": state["notes"], "prev_js": data_js},
            ensure_ascii=False).encode("utf-8"), key)

        # ---- herkese acik, hassas olmayan ozet (yuzde + sembol)
        poss = payload["positions"]
        moves = sorted(((s, (p or {}).get("day_change_pct") or 0.0) for s, p in poss.items()),
                       key=lambda x: -abs(x[1]))
        stops = [e["name"] for e in (payload.get("exit_alerts") or []) if e.get("status") == "stop"]
        tps = [e["name"] for e in (payload.get("exit_alerts") or []) if e.get("status") == "tp"]
        summ = payload.get("summary") or {}
        parts = ["Portföy günü %+.2f%%" % (summ.get("day_change_pct") or 0)]
        if summ.get("best"):
            parts.append("en iyi %s, en zayıf %s" % (summ["best"], summ.get("worst")))
        if moves and abs(moves[0][1]) >= 5:
            parts.append("büyük hareket: " + ", ".join("%s %+.1f%%" % m for m in moves[:3] if abs(m[1]) >= 5))
        if stops:
            parts.append("zarar-kes seviyesinde/altında: " + ", ".join(stops))
        if tps:
            parts.append("kâr-al seviyesinde: " + ", ".join(tps))
        status = {
            "built_at_iso": payload["built_at_iso"],
            "mode": a.mode,
            "quotes_asof_iso": qdoc.get("asof"),
            "html_bytes": len(html.encode("utf-8")),
            "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "positions_ok": len(have),
            "notification": "; ".join(parts) + ".",
        }

        os.makedirs(a.out, exist_ok=True)
        old = a.state
        if os.path.exists(old):
            shutil.copy(old, os.path.join(a.out, "state.prev.enc"))
        open(os.path.join(a.out, "state.enc"), "w", encoding="utf-8").write(new_state)
        open(os.path.join(a.out, "index.html"), "w", encoding="utf-8").write(html)
        json.dump(status, open(os.path.join(a.out, "status.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("TAMAM mod=%s pozisyon=%d html=%dKB" % (a.mode, len(have), len(html) // 1024))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
