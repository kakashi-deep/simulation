"""
TEFAS'tan TÜM yatırım fonlarını (YAT) çeker, data/funds.json olarak yazar.
Ayrıca son 35 günün fiyat geçmişini data/history.json'da biriktirir;
haftalık/aylık getiriler buradan hesaplanır.

Günlük getiri TEFAS bulk endpoint'inde YOK. Bu yüzden bir önceki
funds.json'daki fiyatla karşılaştırıp kendimiz hesaplıyoruz.
'priceDate' alanı (TEFAS 'tarih') sayesinde aynı gün tekrar tetiklenirse
yanlış hesap yapmıyoruz.

NOT: İleride tekrar sadece belirli fonları çekmek istersen, aşağıdaki
ISIM_FILTRE sabitine bir metin yaz (örn. "KATILIM"). None olduğu sürece
filtresiz çalışır.
"""

import json
import os
import sys
from datetime import datetime, timezone

from tefasmak import tum_fonlar, fonlar_son_fiyat_bulk

FON_TIPLERI = ["YAT"]
OUTPUT_PATH = "data/funds.json"
HISTORY_PATH = "data/history.json"
HISTORY_MAX_ENTRIES = 35

# None = filtre yok (tüm fonlar). Metin verilirse sadece isminde o metin
# geçen fonlar JSON'a yazılır. Örn: "KATILIM" → sadece katılım fonları.
ISIM_FILTRE = None


# --- Yardımcılar -------------------------------------------------------------

def _normalize_kod(bilgi, fallback=None):
    if not isinstance(bilgi, dict):
        return (fallback or "").strip().upper()
    return (
        bilgi.get("fonKod")
        or bilgi.get("fonKodu")
        or bilgi.get("kod")
        or bilgi.get("code")
        or fallback
        or ""
    ).strip().upper()


def _normalize_unvan(bilgi):
    if not isinstance(bilgi, dict):
        return ""
    return (
        bilgi.get("unvan")
        or bilgi.get("fonUnvan")
        or bilgi.get("name")
        or bilgi.get("title")
        or ""
    ).strip()


def _normalize_kurucu(bilgi):
    if not isinstance(bilgi, dict):
        return ""
    return (
        bilgi.get("kurucuAd")
        or bilgi.get("kurucu")
        or bilgi.get("founder")
        or bilgi.get("kurucuUnvan")
        or ""
    ).strip()


def _to_float(value):
    """TEFAS bazen '1.234,56' gibi TR formatında string döner; onu da yakala."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("%", "").replace(" ", "")
        if not s:
            return None
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _extract_price(fiyat_bilgi):
    if not isinstance(fiyat_bilgi, dict):
        return None
    for key in ("fiyat", "sonFiyat", "price", "birimPayDegeri", "payDegeri"):
        val = _to_float(fiyat_bilgi.get(key))
        if val is not None:
            return val
    return None


def _matches_filter(unvan: str) -> bool:
    """ISIM_FILTRE None ise hepsi geçer. Değilse isminde arar (büyük/küçük duyarsız)."""
    if not ISIM_FILTRE:
        return True
    return ISIM_FILTRE.upper() in (unvan or "").upper()


# --- Geçmiş (history) yönetimi ----------------------------------------------

def _load_history():
    """{kod: [{date, price}, ...]} — en eskiden en yeniye sıralı."""
    if not os.path.exists(HISTORY_PATH):
        return {}
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        funds = data.get("funds", {})
        return funds if isinstance(funds, dict) else {}
    except Exception as e:
        print(f"UYARI: history.json okunamadı: {e}")
        return {}


def _update_history_entry(history, kod, price, price_date):
    """Bugünün fiyatını geçmişe ekler; aynı tarih varsa günceller."""
    if price is None or not price_date:
        return
    entries = history.get(kod, [])
    entries = [e for e in entries if e.get("date") != price_date]
    entries.append({"date": price_date, "price": price})
    entries.sort(key=lambda x: x.get("date", ""))
    entries = entries[-HISTORY_MAX_ENTRIES:]
    history[kod] = entries


def _compute_returns(entries):
    """Bugünkü fiyatla geçmiş fiyatları karşılaştırarak getirileri hesaplar."""
    result = {
        "weeklyReturn": None,
        "monthlyReturn": None,
        "consecutiveUpDays": 0,
    }
    if not entries or len(entries) < 2:
        return result

    today_price = entries[-1]["price"]

    if len(entries) >= 8:
        old = entries[-8]["price"]
        if old > 0:
            result["weeklyReturn"] = (today_price - old) / old * 100.0

    if len(entries) >= 31:
        old = entries[-31]["price"]
        if old > 0:
            result["monthlyReturn"] = (today_price - old) / old * 100.0

    count = 0
    for i in range(len(entries) - 1, 0, -1):
        if entries[i]["price"] > entries[i - 1]["price"]:
            count += 1
        else:
            break
    result["consecutiveUpDays"] = count

    return result


# --- Ana iş ------------------------------------------------------------------

def build_fund_list(previous, prev_updated_at, history):
    prev_updated_date = (prev_updated_at or "")[:10] or None

    all_funds = []
    seen_symbols = set()
    toplam_gorulen = 0
    filtre_harici_atlanan = 0

    for fon_tipi in FON_TIPLERI:
        print(f"[{fon_tipi}] fon listesi çekiliyor...")
        liste = tum_fonlar(fon_tipi)

        print(f"[{fon_tipi}] güncel fiyat çekiliyor...")
        fiyatlar = fonlar_son_fiyat_bulk(fon_tipi)

        if not isinstance(fiyatlar, dict):
            fiyatlar = {
                _normalize_kod(row): row
                for row in fiyatlar
                if isinstance(row, dict)
            }

        if isinstance(liste, dict):
            items = [
                ({"fonKodu": kod, **bilgi} if isinstance(bilgi, dict) else {"fonKodu": kod})
                for kod, bilgi in liste.items()
            ]
        elif isinstance(liste, list):
            items = liste
        else:
            raise TypeError(f"tum_fonlar() beklenmeyen tip döndürdü: {type(liste)}")

        if items:
            print(f"[{fon_tipi}] örnek kayıt alanları: {list(items[0].keys())}")
        if fiyatlar:
            ornek_fiyat = next(iter(fiyatlar.values()))
            if isinstance(ornek_fiyat, dict):
                print(f"[{fon_tipi}] örnek fiyat alanları: {list(ornek_fiyat.keys())}")

        for bilgi in items:
            if not isinstance(bilgi, dict):
                continue

            kod = _normalize_kod(bilgi)
            if not kod or kod in seen_symbols:
                continue

            unvan = _normalize_unvan(bilgi) or kod

            # ── Filtre (opsiyonel) ──
            if not _matches_filter(unvan):
                filtre_harici_atlanan += 1
                continue

            seen_symbols.add(kod)
            toplam_gorulen += 1

            kurucu = _normalize_kurucu(bilgi)

            fiyat_bilgi = fiyatlar.get(kod, {}) if isinstance(fiyatlar, dict) else {}
            if not isinstance(fiyat_bilgi, dict):
                fiyat_bilgi = {}

            price = _extract_price(fiyat_bilgi)
            price_date = fiyat_bilgi.get("tarih")

            # ── Geçmişi güncelle ──
            _update_history_entry(history, kod, price, price_date)

            # ── Günlük getiri (önceki funds.json ile karşılaştırma) ──
            prev = previous.get(kod) or {}
            prev_price = _to_float(prev.get("price"))
            prev_daily = _to_float(prev.get("dailyReturn"))
            prev_date = prev.get("priceDate") or prev_updated_date

            daily_return = None
            if price is not None and price_date and prev_date:
                if price_date > prev_date and prev_price and prev_price > 0:
                    daily_return = (price - prev_price) / prev_price * 100.0
                elif price_date == prev_date:
                    daily_return = prev_daily
            elif price is not None:
                daily_return = prev_daily

            # ── Haftalık/aylık getiri + üst üste artış ──
            entries = history.get(kod, [])
            returns = _compute_returns(entries)

            all_funds.append({
                "symbol": kod,
                "name": unvan,
                "founder": kurucu,
                "fundType": fon_tipi,
                "price": price,
                "priceDate": price_date,
                "dailyReturn": daily_return,
                "weeklyReturn": returns["weeklyReturn"],
                "monthlyReturn": returns["monthlyReturn"],
                "consecutiveUpDays": returns["consecutiveUpDays"],
                "portfolioSize": _to_float(fiyat_bilgi.get("portfoyBuyukluk")),
                "investorCount": fiyat_bilgi.get("kisiSayisi"),
            })

    if ISIM_FILTRE:
        print(f"Filtre: '{ISIM_FILTRE}' — {toplam_gorulen} fon eşleşti, "
              f"{filtre_harici_atlanan} fon atlandı.")
    else:
        print(f"Filtre yok — {toplam_gorulen} fon alındı.")

    return all_funds


def _sanity_check(funds):
    with_price = sum(1 for f in funds if f.get("price") is not None)
    with_daily = sum(1 for f in funds if f.get("dailyReturn") is not None)
    with_weekly = sum(1 for f in funds if f.get("weeklyReturn") is not None)
    with_monthly = sum(1 for f in funds if f.get("monthlyReturn") is not None)
    with_streak = sum(1 for f in funds if (f.get("consecutiveUpDays") or 0) >= 5)
    print(f"Özet: {len(funds)} fon — fiyat: {with_price}, günlük: {with_daily}, "
          f"haftalık: {with_weekly}, aylık: {with_monthly}, "
          f"5+ gün üst üste artan: {with_streak}")


def main():
    previous = {}
    prev_updated_at = None
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            previous = {
                f["symbol"]: f
                for f in data.get("funds", [])
                if isinstance(f, dict) and f.get("symbol")
            }
            prev_updated_at = data.get("updatedAt")
        except Exception as e:
            print(f"UYARI: Önceki funds.json okunamadı: {e}")

    history = _load_history()
    print(f"Önceki funds.json: {len(previous)} fon "
          f"(updatedAt={prev_updated_at})")
    print(f"Önceki history: {len(history)} fonun geçmişi var")

    try:
        funds = build_fund_list(previous, prev_updated_at, history)
    except Exception as e:
        print(f"HATA: Fon verisi çekilemedi: {e}", file=sys.stderr)
        sys.exit(1)

    if not funds:
        print("HATA: Fon listesi boş döndü, dosya yazılmadı.",
              file=sys.stderr)
        sys.exit(1)

    _sanity_check(funds)

    now = datetime.now(timezone.utc).isoformat()
    output = {
        "updatedAt": now,
        "count": len(funds),
        "filter": ISIM_FILTRE if ISIM_FILTRE else "Yok",
        "funds": funds,
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    history_output = {
        "updatedAt": now,
        "maxEntries": HISTORY_MAX_ENTRIES,
        "funds": history,
    }
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history_output, f, ensure_ascii=False, indent=2)

    print(f"Tamam: {len(funds)} fon -> {OUTPUT_PATH}")
    print(f"Tamam: {len(history)} fonun geçmişi -> {HISTORY_PATH}")


if __name__ == "__main__":
    main()
