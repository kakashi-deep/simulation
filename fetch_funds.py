"""
TEFAS'tan tüm fonların güncel listesini + fiyat/getiri bilgisini çeker,
funds.json olarak yazar. GitHub Actions tarafından periyodik çalıştırılır.

Kullanılan kütüphane: tefasmak (Akamai TSPD korumasını curl_cffi ile aşar)
https://pypi.org/project/tefasmak/
"""

import json
import sys
from datetime import datetime, timezone

from tefasmak import tum_fonlar, fonlar_son_fiyat_bulk

# Şimdilik sadece Yatırım Fonları (YAT). İstenirse "EMK", "BYF" eklenebilir.
FON_TIPLERI = ["YAT"]

OUTPUT_PATH = "data/funds.json"


def _normalize_kod(bilgi, fallback=None):
    return (
        bilgi.get("fonKodu")
        or bilgi.get("kod")
        or bilgi.get("code")
        or fallback
        or ""
    ).strip().upper() if isinstance(bilgi, dict) else (fallback or "")


def _normalize_unvan(bilgi):
    return (
        bilgi.get("fonUnvan")
        or bilgi.get("unvan")
        or bilgi.get("name")
        or bilgi.get("title")
        or ""
    ).strip()


def _normalize_kurucu(bilgi):
    return (
        bilgi.get("kurucu")
        or bilgi.get("founder")
        or bilgi.get("kurucuUnvan")
        or ""
    ).strip()


def build_fund_list():
    all_funds = []
    seen_symbols = set()

    for fon_tipi in FON_TIPLERI:
        print(f"[{fon_tipi}] fon listesi çekiliyor...")
        liste = tum_fonlar(fon_tipi)

        print(f"[{fon_tipi}] güncel fiyat/getiri çekiliyor...")
        fiyatlar = fonlar_son_fiyat_bulk(fon_tipi)  # {kod: {fiyat, gunlukGetiri, ...}}
        if not isinstance(fiyatlar, dict):
            # Bu da liste dönerse koda göre sözlüğe çeviriyoruz.
            fiyatlar = {
                _normalize_kod(row): row
                for row in fiyatlar
                if isinstance(row, dict)
            }

        # tum_fonlar hem liste hem sözlük döndürebilir; ikisini de destekle.
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
                "price": fiyat_bilgi.get("fiyat") or fiyat_bilgi.get("sonFiyat"),
                "dailyReturn": fiyat_bilgi.get("gunlukGetiri"),
            })

    return all_funds


def main():
    try:
        funds = build_fund_list()
    except Exception as e:
        print(f"HATA: Fon verisi çekilemedi: {e}", file=sys.stderr)
        sys.exit(1)

    if not funds:
        print("HATA: Boş fon listesi döndü, dosya yazılmadı.", file=sys.stderr)
        sys.exit(1)

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(funds),
        "funds": funds,
    }

    import os
    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Tamam: {len(funds)} fon yazıldı -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
