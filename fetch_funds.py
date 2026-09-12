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


def build_fund_list():
    all_funds = []
    seen_symbols = set()

    for fon_tipi in FON_TIPLERI:
        print(f"[{fon_tipi}] fon listesi çekiliyor...")
        liste = tum_fonlar(fon_tipi)  # {kod: {unvan, kurucu, ...}} bekleniyor

        print(f"[{fon_tipi}] güncel fiyat/getiri çekiliyor...")
        fiyatlar = fonlar_son_fiyat_bulk(fon_tipi)  # {kod: {fiyat, gunlukGetiri, ...}}

        for kod, bilgi in liste.items():
            if kod in seen_symbols:
                continue
            seen_symbols.add(kod)

            fiyat_bilgi = fiyatlar.get(kod, {})

            all_funds.append({
                "symbol": kod,
                "name": bilgi.get("fonUnvan") or bilgi.get("unvan") or kod,
                "founder": bilgi.get("kurucu", ""),
                "fundType": fon_tipi,
                "price": fiyat_bilgi.get("fiyat"),
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
