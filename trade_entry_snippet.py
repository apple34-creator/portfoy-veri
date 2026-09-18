#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telefondan alim/satim giris ekranini index.html'in STATIK sablonuna ekler.
Bir kerelik elle-calistirilan yama scripti; build.py/inline_data.py zincirinin parcasi
DEGIL. <!--PORTFOLIO-DATA--> blogunun disina, </footer> ile onu izleyen </div> arasina
HTML/CSS/JS ekler; veri blogunu hic gormez.

Kullanim: python3 trade_entry_snippet.py <girdi index.html> <cikti index.html>
"""
import sys

CSS = """
  /* ---- Islem ekle (telefondan alim/satim girisi, db capability) ---- */
  .trade-entry {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); margin-top: 20px; overflow: hidden;
  }
  .trade-entry > summary {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 0 14px; min-height: 48px; cursor: pointer; font-size: 13.5px; font-weight: 600;
    list-style: none;
  }
  .trade-entry > summary::-webkit-details-marker { display: none; }
  .trade-entry > summary::after { content: "\\2795"; color: var(--muted); font-size: 13px; font-weight: 400; }
  .trade-entry[open] > summary::after { content: "\\2796"; }
  .trade-entry .te-count {
    font-size: 11px; font-weight: 700; color: var(--accent); background: var(--accent-soft);
    padding: 2px 7px; border-radius: 999px;
  }
  .te-body { padding: 4px 14px 16px; display: flex; flex-direction: column; gap: 12px; }
  .te-status { font-size: 12.5px; color: var(--muted); }
  #te-form { display: flex; flex-direction: column; gap: 10px; }
  .te-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .te-row label, .te-note-label {
    display: flex; flex-direction: column; gap: 4px; font-size: 11px;
    color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600;
    min-width: 0;
  }
  #te-form select, #te-form input {
    width: 100%; min-width: 0; max-width: 100%;
    font-size: 15px; padding: 9px 10px; border-radius: var(--radius-sm);
    border: 1px solid var(--border); background: var(--page); color: var(--ink);
    font-family: inherit;
  }
  #te-submit {
    font-size: 14px; font-weight: 700; padding: 11px 14px; border-radius: var(--radius-sm);
    border: none; background: var(--accent); color: #fff; cursor: pointer;
  }
  #te-submit:disabled { opacity: 0.6; cursor: default; }
  .te-hint { font-size: 11.5px; color: var(--muted); line-height: 1.5; margin: 0; }
  .te-sub {
    font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--muted); font-weight: 600; margin-top: 4px;
  }
  .te-row-item {
    display: flex; align-items: center; gap: 10px; padding: 8px 0;
    border-top: 1px solid var(--grid); font-size: 13px;
  }
  .te-row-item .te-sym { font-weight: 700; min-width: 44px; }
  .te-row-item .te-side { font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 2px 6px; border-radius: 5px; }
  .te-row-item .te-side.buy { color: var(--up); background: var(--up-soft); }
  .te-row-item .te-side.sell { color: var(--down); background: var(--down-soft); }
  .te-row-item .te-cancel {
    margin-left: auto; font-size: 11.5px; color: var(--down); background: none;
    border: 1px solid var(--down); border-radius: 999px; padding: 3px 9px; cursor: pointer;
  }
  .te-row-item .te-cancel:disabled { opacity: 0.5; cursor: default; }
  /* ---- Karar Gunlugu (neden aldim/sattim + o gunden bugune) ---- */
  .karar-gunlugu {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); margin-top: 20px; overflow: hidden;
  }
  .karar-gunlugu > summary {
    display: flex; align-items: center; gap: 10px;
    padding: 0 14px; min-height: 48px; cursor: pointer; font-size: 13.5px; font-weight: 600;
    list-style: none;
  }
  .karar-gunlugu > summary::-webkit-details-marker { display: none; }
  .karar-gunlugu > summary::after { content: "\\2795"; color: var(--muted); font-size: 13px; margin-left: auto; }
  .karar-gunlugu[open] > summary::after { content: "\\2796"; }
  .karar-gunlugu .kg-count {
    font-size: 11px; font-weight: 700; color: var(--muted); background: var(--surface-2);
    padding: 2px 7px; border-radius: 999px;
  }
  .kg-body { padding: 0 14px 14px; }
  .kg-item { padding: 11px 0; border-top: 1px solid var(--grid); }
  .kg-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12.5px; }
  .kg-date { color: var(--muted); font-variant-numeric: tabular-nums; }
  .kg-sym { font-weight: 700; font-size: 13px; }
  .kg-side { font-size: 10.5px; font-weight: 700; text-transform: uppercase; padding: 2px 6px; border-radius: 5px; }
  .kg-side.buy { color: var(--up); background: var(--up-soft); }
  .kg-side.sell { color: var(--down); background: var(--down-soft); }
  .kg-qty { color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .kg-since { margin-left: auto; font-weight: 700; font-variant-numeric: tabular-nums; font-size: 12.5px; }
  .kg-since.up { color: var(--up); }
  .kg-since.down { color: var(--down); }
  .kg-since.flat { color: var(--muted); font-weight: 600; }
  .kg-note { font-size: 13px; line-height: 1.5; color: var(--ink); margin-top: 5px; }
  .kg-note.empty { color: var(--muted); font-style: italic; }
  .kg-foot { font-size: 11.5px; color: var(--muted); margin-top: 10px; line-height: 1.5; }
"""

HTML = """
  <details class="trade-entry" id="trade-entry">
    <summary>
      <span>İşlem Ekle</span>
      <span class="te-count" id="te-pending-count" hidden></span>
    </summary>
    <div class="te-body">
      <div class="te-status" id="te-status">Yükleniyor…</div>
      <form id="te-form" hidden>
        <div class="te-row">
          <label>Sembol
            <select id="te-symbol" required></select>
          </label>
          <label>İşlem
            <select id="te-side" required>
              <option value="buy">Al</option>
              <option value="sell">Sat</option>
            </select>
          </label>
        </div>
        <div class="te-row">
          <label>Adet
            <input id="te-shares" type="number" step="any" min="0" required inputmode="decimal">
          </label>
          <label>Fiyat ($/hisse)
            <input id="te-price" type="number" step="any" min="0" required inputmode="decimal">
          </label>
        </div>
        <label class="te-note-label">Neden? (zorunlu)
          <input id="te-note" type="text" maxlength="200" required
                 placeholder="örn. AI veri merkezi talebi hızlanıyor, 2 yıl tutmayı planlıyorum">
        </label>
        <button type="submit" id="te-submit">Kuyruğa Ekle</button>
        <p class="te-hint">Bu bir alım/satım emri değildir — yalnızca kayıt kuyruğuna eklenir. Bir sonraki rutin çalışması (birkaç saat içinde) bunu okuyup portföye işler. <strong>Gerekçe zorunlu:</strong> yazdığın cümle Karar Günlüğü'ne geçer ve aylar sonra o günün fiyatıyla birlikte sana geri gösterilir. Yalnızca mevcut 13 hisseden biri işlenebilir; yeni bir hisse eklemek için ayrıca söyle.</p>
      </form>
      <div class="te-pending" id="te-pending"></div>
    </div>
  </details>
"""

# Karar Gunlugu bolumu: islem-ekle blogundan AYRI, <footer>'dan ONCE durur (sayfada
# "Aday Hisseler"den sonraki son gorunur bolum). Bos gunlukte JS bunu gizli tutar.
KG_HTML = """  <details class="karar-gunlugu" id="karar-gunlugu" hidden>
    <summary>
      <span>Karar Günlüğü</span>
      <span class="kg-count" id="kg-count"></span>
    </summary>
    <div class="kg-body" id="kg-body"></div>
  </details>
"""

SCRIPT = """
<script>
(function () {
  "use strict";
  var statusEl = document.getElementById("te-status");
  var formEl = document.getElementById("te-form");
  var symbolEl = document.getElementById("te-symbol");
  var sideEl = document.getElementById("te-side");
  var sharesEl = document.getElementById("te-shares");
  var priceEl = document.getElementById("te-price");
  var noteEl = document.getElementById("te-note");
  var submitBtn = document.getElementById("te-submit");
  var pendingEl = document.getElementById("te-pending");
  var countEl = document.getElementById("te-pending-count");
  if (!statusEl || !formEl) return;

  var port = window.__PORTFOLIO__ || {};
  var order = port.order || [];
  var positions = port.positions || {};
  var processedIds = {};
  (port.processed_trade_ids || []).forEach(function (id) { processedIds[id] = true; });
  order.forEach(function (sym) {
    var opt = document.createElement("option");
    opt.value = sym;
    opt.textContent = sym + " \\u2014 " + ((positions[sym] || {}).name || sym);
    symbolEl.appendChild(opt);
  });

  var col = null, readonly = false, busy = false;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtMoney(n) {
    var v = Number(n);
    return "$" + (isFinite(v) ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "?");
  }

  function renderPending(allDocs) {
    var docs = allDocs.filter(function (d) { return d.data.status === "pending" && !processedIds[d.id]; });
    if (!docs.length) { pendingEl.innerHTML = ""; countEl.hidden = true; return; }
    countEl.hidden = false; countEl.textContent = docs.length;
    pendingEl.innerHTML = "<div class='te-sub'>Bekleyen (" + docs.length + ")</div>" +
      docs.map(function (d) {
        var t = d.data;
        return "<div class='te-row-item'>" +
          "<span class='te-sym'>" + esc(t.symbol) + "</span>" +
          "<span class='te-side " + (t.side === "sell" ? "sell" : "buy") + "'>" + (t.side === "sell" ? "Sat" : "Al") + "</span>" +
          "<span>" + esc(t.shares) + " @ " + fmtMoney(t.price) + "</span>" +
          "<button type='button' class='te-cancel' data-id='" + esc(d.id) + "'" + (readonly ? " disabled" : "") + ">İptal</button>" +
          "</div>";
      }).join("");
    Array.prototype.forEach.call(pendingEl.querySelectorAll(".te-cancel"), function (btn) {
      btn.addEventListener("click", function () {
        if (!col || readonly) return;
        btn.disabled = true;
        col.doc(btn.getAttribute("data-id")).delete().catch(function () { btn.disabled = false; });
      });
    });
  }

  // Rutin, islenen kayitlarin durumunu ArtifactData'ya YAZMAZ (bulut rutininde izin
  // istemi cikarip askida kalirdi) -- islenen id'ler window.__PORTFOLIO__.processed_trade_ids
  // icinde tasinir. Sayfa bunu goren, hala "pending" duran eski belgeleri kendi (goruntuleyici)
  // yetkisiyle sessizce temizler.
  function cleanupProcessed(allDocs) {
    if (!col || readonly) return;
    allDocs.forEach(function (d) {
      if (d.data.status === "pending" && processedIds[d.id]) {
        col.doc(d.id).delete().catch(function () {});
      }
    });
  }

  formEl.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!col || readonly || busy) return;
    var shares = parseFloat(sharesEl.value), price = parseFloat(priceEl.value);
    var noteVal = (noteEl.value || "").trim();
    if (!symbolEl.value || !(shares > 0) || !(price > 0)) return;
    if (noteVal.length < 3) {                      // gerekce zorunlu: gunlugun tum degeri burada
      noteEl.focus();
      statusEl.hidden = false;
      statusEl.textContent = "Gerekçe yazman gerekiyor — tek cümle yeter.";
      return;
    }
    statusEl.hidden = true;
    busy = true; submitBtn.disabled = true; submitBtn.textContent = "Ekleniyor\\u2026";
    var id = "t" + Date.now();
    col.doc(id).set({
      symbol: symbolEl.value, side: sideEl.value, shares: shares, price: price,
      note: noteVal.slice(0, 200), status: "pending",
      submitted_at: new Date().toISOString()
    }).then(function () {
      formEl.reset();
    }).catch(function () {
      alert("Eklenemedi, tekrar dene.");
    }).then(function () {
      busy = false; submitBtn.disabled = false; submitBtn.textContent = "Kuyru\\u011fa Ekle";
    });
  });


  // ---- Karar Gunlugu: gecmis kararlari o gunden bugune degisimle goster ----
  var decisions = (port.decisions || []).slice();
  function renderDecisions() {
    var wrap = document.getElementById("karar-gunlugu");
    var body = document.getElementById("kg-body");
    var cnt = document.getElementById("kg-count");
    if (!wrap || !body) return;
    if (!decisions.length) { wrap.hidden = true; return; }   // bos gunluk hic gorunmez
    wrap.hidden = false;
    var rows = decisions.slice().reverse();                  // en yeni ustte
    cnt.textContent = rows.length + " karar";
    body.innerHTML = rows.map(function (d) {
      var pos = positions[d.symbol];
      var since = "";
      if (pos && pos.current_price && d.price > 0) {
        var pct = (pos.current_price - d.price) / d.price * 100;
        var cls = pct > 0.05 ? "up" : (pct < -0.05 ? "down" : "flat");
        var sign = pct > 0 ? "+" : "";
        since = "<span class='kg-since " + cls + "'>" + sign + pct.toFixed(1) + "%</span>";
      } else if (!pos) {
        since = "<span class='kg-since flat'>pozisyon kapandı</span>";
      }
      var qty = (d.shares != null && d.price != null)
        ? "<span class='kg-qty'>" + esc(d.shares) + " @ " + fmtMoney(d.price) + "</span>" : "";
      var note = d.note
        ? "<div class='kg-note'>" + esc(d.note) + "</div>"
        : "<div class='kg-note empty'>(gerekçe yazılmamış)</div>";
      return "<div class='kg-item'>" +
        "<div class='kg-head'>" +
          "<span class='kg-date'>" + esc(d.date || "") + "</span>" +
          "<span class='kg-sym'>" + esc(d.symbol || "") + "</span>" +
          "<span class='kg-side " + (d.side === "sell" ? "sell" : "buy") + "'>" +
            (d.side === "sell" ? "Sat" : "Al") + "</span>" +
          qty + since +
        "</div>" + note +
      "</div>";
    }).join("") +
    "<div class='kg-foot'>Yüzde, o işlemin fiyatından bugünkü fiyata değişimdir — " +
    "kârın değil, kararın nasıl yaşlandığının ölçüsü. Pazar günü rutini her hafta " +
    "bunlardan birini bildirimle hatırlatır.</div>";
  }
  renderDecisions();

  if (!(window.claude && window.claude.use)) {
    statusEl.textContent = "\\u0130\\u015flem eklemek i\\u00e7in bu sayfay\\u0131 Claude i\\u00e7inden a\\u00e7 (taray\\u0131c\\u0131 sekmesinde de\\u011fil).";
    return;
  }
  window.claude.use("db").then(function (db) {
    if (!db) { statusEl.textContent = "\\u0130\\u015flem ekleme \\u015fu an kullan\\u0131lam\\u0131yor."; return; }
    col = db.collection("trades");
    statusEl.hidden = true; formEl.hidden = false;
    col.onSnapshot(function (snap) {
      var docs = snap.docs.map(function (d) { return { id: d.id, data: d.data() }; });
      renderPending(docs);
      cleanupProcessed(docs);
    }, function (err) {
      var code = err && err.code;
      if (code === "revoked" || code === "not_granted") {
        readonly = true;
        statusEl.hidden = false; statusEl.textContent = "Bu g\\u00f6r\\u00fcn\\u00fcmde i\\u015flem eklenemiyor (salt okunur).";
        formEl.hidden = true;
      }
    });
  }).catch(function () {
    statusEl.textContent = "\\u0130\\u015flem ekleme \\u015fu an kullan\\u0131lam\\u0131yor.";
  });
})();
</script>
"""


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("kullanim: trade_entry_snippet.py <girdi> <cikti>\n")
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    html = open(src, encoding="utf-8").read()

    style_anchor = "</style>"
    if CSS.strip()[:20] in html:
        sys.stderr.write("UYARI: CSS zaten eklenmis gorunuyor, tekrar eklenmiyor.\n")
    else:
        idx = html.index(style_anchor)
        html = html[:idx] + CSS + html[idx:]

    footer_anchor = "</footer>\n</div>"
    if footer_anchor not in html:
        sys.stderr.write("HATA: </footer>\\n</div> ankoru bulunamadi.\n")
        return 1
    if 'id="trade-entry"' in html:
        sys.stderr.write("UYARI: trade-entry HTML zaten var, tekrar eklenmiyor.\n")
    else:
        html = html.replace(footer_anchor, "</footer>\n" + HTML + "</div>", 1)

    kg_anchor = "  <footer>"
    if "karar-gunlugu" in html and 'id="karar-gunlugu"' in html:
        sys.stderr.write("UYARI: Karar Gunlugu bolumu zaten var, tekrar eklenmiyor.\n")
    elif kg_anchor not in html:
        sys.stderr.write("HATA: '  <footer>' ankoru bulunamadi (Karar Gunlugu eklenemedi).\n")
        return 1
    else:
        html = html.replace(kg_anchor, KG_HTML + kg_anchor, 1)

    if 'id="te-form"' in html and "te-pending-count" in html and html.rstrip().endswith("</script>"):
        # script'i sona ekle (yalnizca daha once eklenmediyse)
        if "col.doc(id).set(" not in html:
            html = html.rstrip() + "\n" + SCRIPT
    else:
        html = html.rstrip() + "\n" + SCRIPT

    with open(dst, "w", encoding="utf-8") as f:
        f.write(html)
    print("Yazildi: %s (%d bayt)" % (dst, len(html)))


if __name__ == "__main__":
    main()
