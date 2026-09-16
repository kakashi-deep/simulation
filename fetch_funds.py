"""
TEFAS'tan sadece isminde 'Katılım' geçen fonları çeker (faizsiz fonlar),
data/funds.json olarak yazar. GitHub Actions tarafından periyodik çalıştırılır.

Günlük getiri TEFAS bulk endpoint'inde YOK. Bu yüzden bir önceki
funds.json'daki fiyatla karşılaştırıp kendimiz hesaplıyoruz.
'priceDate' alanı (TEFAS 'tarih') sayesinde aynı gün tekrar tetiklenirse
yanlış hesap yapmıyoruz.
"""

import json
import os
import sys
from datetime import datetime, timezone

from tefasmak import tum_fonlar, fonlar_son_fiyat_bulk

FON_TIPLERI = ["YAT"]
OUTPUT_PATH = "data/funds.json"

# Fon isminde (unvanda) aranacak anahtar kelime. Türkçe büyük/küçük harf
# farkını Python'un .upper()'ı tolere ettiği için "KATILIM" sabiti yeterli:
#   "katılım".upper() == "KATILIM"
#   "Katılım".upper() == "KATILIM"
ISIM_FILTRE = "KATILIM"


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


def _is_katilim_fonu(unvan: str) -> bool:
    """İsminde 'Katılım' geçiyor mu? Türkçe büyük/küçük harf duyarsız."""
    return ISIM_FILTRE in (unvan or "").upper()


def _load_previous():
    """Önceki funds.json'u döner: (kod -> kayıt sözlüğü, updatedAt)."""
    if not os.path.exists(OUTPUT_PATH):
        return {}, None
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        funds = data.get("funds", [])
        prev = {
            f["symbol"]: f
            for f in funds
            if isinstance(f, dict) and f.get("symbol")
        }
        return prev, data.get("updatedAt")
    except Exception as e:
        print(f"UYARI: Önceki funds.json okunamadı: {e}")
        return {}, None


# --- Ana iş ------------------------------------------------------------------

def build_fund_list(previous, prev_updated_at=None):
    prev_updated_date = (prev_updated_at or "")[:10] or None

    all_funds = []
    seen_symbols = set()
    toplam_gorulen = 0
    katilim_harici_atlanan = 0

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

            # ─── İsim filtresi: sadece 'Katılım' içeren fonlar ────────
            if not _is_katilim_fonu(unvan):
                katilim_harici_atlanan += 1
                continue

            seen_symbols.add(kod)
            toplam_gorulen += 1

            kurucu = _normalize_kurucu(bilgi)

            fiyat_bilgi = fiyatlar.get(kod, {}) if isinstance(fiyatlar, dict) else {}
            if not isinstance(fiyat_bilgi, dict):
                fiyat_bilgi = {}

            price = _extract_price(fiyat_bilgi)
            price_date = fiyat_bilgi.get("tarih")

            # ─── Günlük getiriyi önceki fiyatla karşılaştırarak hesapla ──
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

            all_funds.append({
                "symbol": kod,
                "name": unvan,
                "founder": kurucu,
                "fundType": fon_tipi,
                "price": price,
                "priceDate": price_date,
                "dailyReturn": daily_return,
                "portfolioSize": _to_float(fiyat_bilgi.get("portfoyBuyukluk")),
                "investorCount": fiyat_bilgi.get("kisiSayisi"),
            })

    print(f"Filtre: '{ISIM_FILTRE}' — {toplam_gorulen} fon eşleşti, "
          f"{katilim_harici_atlanan} fon atlandı.")

    return all_funds


def _sanity_check(funds):
    with_price = sum(1 for f in funds if f.get("price") is not None)
    with_date = sum(1 for f in funds if f.get("priceDate"))
    with_daily = sum(1 for f in funds if f.get("dailyReturn") is not None)
    print(f"Özet: {len(funds)} fon — fiyat dolu: {with_price}, "
          f"tarih dolu: {with_date}, günlük getiri dolu: {with_daily}")

    ornek = next((f for f in funds if f.get("dailyReturn") is not None), None)
    if ornek:
        print(f"Örnek günlük getiri: {ornek['symbol']} "
              f"fiyat={ornek['price']} tarih={ornek.get('priceDate')} "
              f"gunluk={ornek['dailyReturn']:.4f}%")


def main():
    previous, prev_updated_at = _load_previous()
    print(f"Önceki funds.json: {len(previous)} fon "
          f"(updatedAt={prev_updated_at})")

    try:
        funds = build_fund_list(previous, prev_updated_at)
    except Exception as e:
        print(f"HATA: Fon verisi çekilemedi: {e}", file=sys.stderr)
        sys.exit(1)

    if not funds:
        print("HATA: Filtreye uyan fon bulunamadı, dosya yazılmadı.",
              file=sys.stderr)
        sys.exit(1)

    _sanity_check(funds)

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(funds),
        "filter": "Katılım",
        "funds": funds,
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Tamam: {len(funds)} fon yazıldı -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
