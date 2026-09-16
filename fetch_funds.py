"""
TEFAS'tan tüm fonların güncel listesini + fiyat/getiri bilgisini çeker,
data/funds.json olarak yazar. GitHub Actions tarafından periyodik çalıştırılır.

Kullanılan kütüphane: tefasmak (Akamai TSPD korumasını curl_cffi ile aşar)
https://pypi.org/project/tefasmak/
"""

import json
import os
import sys
from datetime import datetime, timezone

from tefasmak import tum_fonlar, fonlar_son_fiyat_bulk

# Şimdilik sadece Yatırım Fonları (YAT). İstenirse "EMK", "BYF" eklenebilir.
FON_TIPLERI = ["YAT"]

OUTPUT_PATH = "data/funds.json"


# --- Alan normalizasyon yardımcıları ----------------------------------------

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
        # 1.234,56 -> 1234.56
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
    """Fiyat alanı için birden fazla olası anahtarı dene."""
    if not isinstance(fiyat_bilgi, dict):
        return None
    for key in ("fiyat", "sonFiyat", "price", "birimPayDegeri", "payDegeri"):
        val = _to_float(fiyat_bilgi.get(key))
        if val is not None:
            return val
    return None


def _extract_daily_return(fiyat_bilgi):
    """Günlük getiri (%) için birden fazla olası anahtarı dene."""
    if not isinstance(fiyat_bilgi, dict):
        return None
    for key in (
        "gunlukGetiri",
        "gunlukGetiriYuzde",
        "gunlukGetiriYüzde",
        "dailyReturn",
        "getiri",
        "gunlukDegisim",
    ):
        val = _to_float(fiyat_bilgi.get(key))
        if val is not None:
            return val
    return None


# --- Ana iş ------------------------------------------------------------------

def build_fund_list():
    all_funds = []
    seen_symbols = set()

    for fon_tipi in FON_TIPLERI:
        print(f"[{fon_tipi}] fon listesi çekiliyor...")
        liste = tum_fonlar(fon_tipi)

        print(f"[{fon_tipi}] güncel fiyat/getiri çekiliyor...")
        fiyatlar = fonlar_son_fiyat_bulk(fon_tipi)

        # Bulk bazen liste döner; koda göre sözlüğe çevir.
        if not isinstance(fiyatlar, dict):
            fiyatlar = {
                _normalize_kod(row): row
                for row in fiyatlar
                if isinstance(row, dict)
            }

        # tum_fonlar hem liste hem sözlük döndürebilir.
        if isinstance(liste, dict):
            items = [
                ({"fonKodu": kod, **bilgi} if isinstance(bilgi, dict) else {"fonKodu": kod})
                for kod, bilgi in liste.items()
            ]
        elif isinstance(liste, list):
            items = liste
        else:
            raise TypeError(f"tum_fonlar() beklenmeyen tip döndürdü: {type(liste)}")

        # --- Debug: gerçek alan adlarını logla (ilk kayıtlardan) ---
        if items:
            print(f"[{fon_tipi}] örnek kayıt alanları: {list(items[0].keys())}")
        if fiyatlar:
            ornek_fiyat = next(iter(fiyatlar.values()))
            if isinstance(ornek_fiyat, dict):
                print(f"[{fon_tipi}] örnek fiyat alanları: {list(ornek_fiyat.keys())}")
                print(f"[{fon_tipi}] örnek fiyat değeri: {ornek_fiyat}")

        for bilgi in items:
            if not isinstance(bilgi, dict):
                continue

            kod = _normalize_kod(bilgi)
            if not kod or kod in seen_symbols:
                continue
            seen_symbols.add(kod)

            unvan = _normalize_unvan(bilgi) or kod
            kurucu = _normalize_kurucu(bilgi)

            fiyat_bilgi = fiyatlar.get(kod, {}) if isinstance(fiyatlar, dict) else {}
            if not isinstance(fiyat_bilgi, dict):
                fiyat_bilgi = {}

            all_funds.append({
                "symbol": kod,
                "name": unvan,
                "founder": kurucu,
                "fundType": fon_tipi,
                "price": _extract_price(fiyat_bilgi),
                "dailyReturn": _extract_daily_return(fiyat_bilgi),
            })

    return all_funds


def _sanity_check(funds):
    """Kaç fonun fiyatı ve günlük getirisi dolu geldi, logla."""
    with_price = sum(1 for f in funds if f.get("price") is not None)
    with_daily = sum(1 for f in funds if f.get("dailyReturn") is not None)
    print(f"Özet: {len(funds)} fon — "
          f"fiyat dolu: {with_price}, günlük getiri dolu: {with_daily}")

    ornek = next(
        (f for f in funds if f.get("price") is not None and f.get("dailyReturn") is not None),
        None,
    )
    if ornek:
        print(f"Örnek dolu kayıt: {ornek['symbol']} "
              f"fiyat={ornek['price']} gunluk={ornek['dailyReturn']}")
    else:
        print("UYARI: Fiyat + gunlukGetiri birlikte dolu tek kayıt bile yok! "
              "tefasmak sürümü alan adını değiştirmiş olabilir.")


def main():
    try:
        funds = build_fund_list()
    except Exception as e:
        print(f"HATA: Fon verisi çekilemedi: {e}", file=sys.stderr)
        sys.exit(1)

    if not funds:
        print("HATA: Boş fon listesi döndü, dosya yazılmadı.", file=sys.stderr)
        sys.exit(1)

    _sanity_check(funds)

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(funds),
        "funds": funds,
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Tamam: {len(funds)} fon yazıldı -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
