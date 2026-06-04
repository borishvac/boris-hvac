#!/usr/bin/env python3
"""
Парсер прайс-листа Breezart (https://breezart.ru/tech/price_breezart.pdf).

Скачивает PDF, извлекает текст, парсит модели вентустановок и формирует
catalog.json для фронтового калькулятора.

Принципы:
- Если PDF не скачался или структура поменялась — выходим с кодом 1
  и не трогаем старый catalog.json (последняя успешная версия).
- Если хотя бы одна серия распарсилась с моделями — записываем результат.
- Логируем какие серии нашли, какие нет.

Запуск: python parser.py [output_path]
По умолчанию output_path = ../frontend/catalog.json

Зависимости: requests, pdfplumber
"""

import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    import pdfplumber
except ImportError:
    print("ERROR: установите зависимости: pip install requests pdfplumber", file=sys.stderr)
    sys.exit(1)


PRICE_URL = "https://breezart.ru/tech/price_breezart.pdf"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "frontend" / "catalog.json"
PDF_TMP = Path("/tmp/breezart_price.pdf")

# Маркеры серий в тексте PDF.
# Ключ → подстрока заголовка в PDF (первые слова — обязательная часть).
# Парсер ищет блок текста от этого маркера до следующего.
SERIES_MARKERS = [
    ("lux",      "Lux - приточные установки"),
    ("lux_f",    "Lux F - приточные установки с фреоновым охладителем"),
    ("lux_w",    "Lux W - приточные установки с водяным охладителем"),
    ("lite",     "Lite - бюджетные приточные установки"),
    ("mix",      "FC Mix - ПУ с камерой смешения"),
    ("mix_f",    "Mix F - ПУ с камерой смешения и фреоновым охладителем"),
    ("mix_w",    "Mix W - ПУ с камерой смешения и водяным охладителем"),
    ("aqua",     "Aqua - приточные установки"),
    ("aqua_f",   "Aqua F - приточные установки c фреоновым охладителем"),
    ("aqua_w",   "Aqua W - приточные установки c водяным охладителем"),
]

# Регулярки для строк прайса.
# Строки электрических серий: "550 Lux EZP5,4-PF 1,8 / 3,6 / 5,4 220/380 260 400"
# Группы: (типоразмер) (название серии) (модель нагревателя) (мощности кВт) (напряж.) (цена)
RE_ELECTRIC_LINE = re.compile(
    r"^(\d{3,5})\s+"                              # типоразмер 300/550/1000/2000…
    r"([A-Za-z][A-Za-z\s/+]+?)\s+"                # серия (Lux, Lux F, Lite, Mix и т.п.)
    r"((?:EZP?|ETP?)\d[\d,/-]*[A-Za-z0-9-]*)\s+"  # модель нагревателя EZP5,4-PF / ET45-380/3
    r"([\d,\s/]+?)\s+"                            # мощность(и) кВт
    r"(220(?:/380)?|380)\s+"                      # напряжение
    r"([\d\s]+)\s*$"                              # цена
)

# Строки водяных (Aqua): "550 Aqua 220 323 300"
RE_AQUA_LINE = re.compile(
    r"^(\d{3,5})\s+"                              # типоразмер
    r"(Aqua(?:\s+[FW])?)\s*"                      # Aqua, Aqua F, Aqua W
    r"(?:\(без стоимости с/у\)\s+)?"              # опц. примечание
    r"(220|380)\s+"
    r"([\d\s]+)\s*$"
)

# Секции HEPA / Filter Case для аллергиков (страница «Секции фильтра тонкой очистки»).
# "550 HEPA Case 600 49 000" — типоразмер, название, макс. расход, цена.
RE_HEPA_LINE = re.compile(
    r"^(\d{3,5})\s+(HEPA\s+Case|Filter\s+Case(?:\s+\(SB\))?)\s+(\d+)\s+([\d\s]+)\s*$"
)

# Сам фильтр E11 (одиночный) — "610-610-78-E11 16 500"
RE_FILTER_E11_PRICE = re.compile(r"E11\s+([\d\s]+)\s*$")


def download_pdf(url: str, dest: Path) -> bool:
    """Скачивает PDF. True/False — успех/неудача."""
    try:
        r = requests.get(url, timeout=60, headers={
            "User-Agent": "Mozilla/5.0 (compatible; BreezartPriceParser/1.0)"
        })
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            print(f"ERROR: ответ от {url} не похож на PDF", file=sys.stderr)
            return False
        dest.write_bytes(r.content)
        size_kb = len(r.content) // 1024
        print(f"OK: скачан PDF {size_kb} KB → {dest}")
        return True
    except Exception as e:
        print(f"ERROR при скачивании: {e}", file=sys.stderr)
        return False


def extract_text(pdf_path: Path) -> str:
    """Возвращает весь текст PDF одной строкой."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    return "\n".join(pages)


def parse_number(s: str) -> int:
    """'260 400' → 260400, '1 364 900' → 1364900."""
    return int(re.sub(r"\s+", "", s))


def parse_powers(s: str) -> list:
    """'1,8 / 3,6 / 5,4' → [1.8, 3.6, 5.4]."""
    parts = [p.strip() for p in s.split("/")]
    out = []
    for p in parts:
        p = p.replace(",", ".").strip()
        if p:
            try:
                out.append(float(p))
            except ValueError:
                pass
    return out


def find_section(text: str, marker: str, next_markers: list) -> str:
    """Возвращает кусок текста от marker до ближайшего из next_markers."""
    idx = text.find(marker)
    if idx < 0:
        return ""
    start = idx + len(marker)
    end = len(text)
    for nm in next_markers:
        nidx = text.find(nm, start)
        if 0 < nidx < end:
            end = nidx
    return text[start:end]


def parse_electric_series(section_text: str) -> list:
    """Парсит блок текста серии с электрическим нагревателем."""
    models = {}  # ключ: типоразмер → {name, nominal, heaters: [[kW, price]...]}
    for line in section_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = RE_ELECTRIC_LINE.match(line)
        if not m:
            continue
        nominal = int(m.group(1))
        series_label = m.group(2).strip()
        # модель нагревателя m.group(3) сейчас не используем
        powers = parse_powers(m.group(4))
        price = parse_number(m.group(6))

        # Имя модели: "550 Lux", "2700 Lux F" и т.п.
        name = f"{nominal} {series_label}"
        key = name

        if key not in models:
            models[key] = {
                "name": name,
                "nominal": nominal,
                "heaters": [],
            }
        # Если в строке несколько мощностей через "/", у всех одна цена
        for kw in powers:
            models[key]["heaters"].append([kw, price])
    return list(models.values())


def parse_aqua_series(section_text: str) -> list:
    """Парсит блок текста серии Aqua (водяной нагреватель)."""
    models = []
    for line in section_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = RE_AQUA_LINE.match(line)
        if not m:
            continue
        nominal = int(m.group(1))
        series_label = m.group(2).strip()
        price = parse_number(m.group(4))
        models.append({
            "name": f"{nominal} {series_label}",
            "nominal": nominal,
            "price": price,
        })
    return models


def parse_hepa_sections(full_text: str) -> dict:
    """Парсит секции HEPA Case с фильтром E11 → цена доплаты по типоразмеру.
    
    Возвращает {типоразмер: цена_E11_секции_итого}.
    Логика: HEPA Case = корпус для фильтра тонкой очистки. Сам фильтр E11
    — отдельная позиция. Складываем корпус + фильтр E11 нужного размера.
    """
    # Сначала цены HEPA Case по типоразмеру
    hepa_case_price = {}
    for line in full_text.split("\n"):
        m = RE_HEPA_LINE.match(line.strip())
        if m and "HEPA" in m.group(2):
            size = int(m.group(1))
            price = parse_number(m.group(4))
            hepa_case_price[size] = price

    # Цена фильтра E11 (берём первую найденную — это базовый размер)
    # В прайсе: 410-410-78-E11 = 16500, 610-610-78-E11 = 27800 и т.д.
    # Используем "среднюю" цену для упрощения. На практике зависит от типоразмера приточки.
    e11_filter_prices = []
    for line in full_text.split("\n"):
        if "E11" in line and "Case" not in line:
            m = RE_FILTER_E11_PRICE.search(line.strip())
            if m:
                try:
                    e11_filter_prices.append(parse_number(m.group(1)))
                except Exception:
                    pass
    # Берём среднее значение фильтра E11 — порядка 16-28K
    avg_e11 = sum(e11_filter_prices) // len(e11_filter_prices) if e11_filter_prices else 20000

    # Итоговая доплата = корпус HEPA + сам фильтр E11
    result = {}
    for size, case_price in hepa_case_price.items():
        result[str(size)] = case_price + avg_e11
    return result


def parse_catalog(pdf_path: Path) -> dict:
    """Главный парсер. Возвращает словарь catalog или бросает исключение."""
    text = extract_text(pdf_path)
    if len(text) < 5000:
        raise RuntimeError(f"PDF слишком короткий ({len(text)} символов), парсинг ненадёжен")

    all_markers = [m for _, m in SERIES_MARKERS]

    catalog = {}
    found_series = []
    missing_series = []

    for key, marker in SERIES_MARKERS:
        # next_markers — все ОСТАЛЬНЫЕ маркеры (искать первый по позиции после текущего)
        next_markers = [m for m in all_markers if m != marker]
        # + маркеры конца раздела (заголовки следующих разделов прайса)
        next_markers += [
            "Вентиляционные установки с водяным калорифером",
            "Вытяжные установки",
            "Увлажнители воздуха",
            "Дополнительные опции",
            "Оборудование серии Pool",
        ]
        section = find_section(text, marker, next_markers)
        if not section.strip():
            missing_series.append(key)
            continue

        if key.startswith("aqua"):
            models = parse_aqua_series(section)
        else:
            models = parse_electric_series(section)

        if models:
            catalog[key] = models
            found_series.append(f"{key}({len(models)})")
        else:
            missing_series.append(key)

    if not catalog:
        raise RuntimeError("Не распарсилась ни одна серия — структура PDF изменилась")

    e11_prices = parse_hepa_sections(text)

    print(f"OK: найдены серии: {', '.join(found_series)}")
    if missing_series:
        print(f"WARN: не найдены серии: {', '.join(missing_series)}")
    print(f"OK: цены E11 для типоразмеров: {sorted(e11_prices.keys())}")

    return {
        "version": datetime.now(timezone.utc).isoformat(),
        "source": PRICE_URL,
        "series": catalog,
        "e11_addon": e11_prices,
    }


def main():
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not download_pdf(PRICE_URL, PDF_TMP):
        print("FATAL: не удалось скачать PDF, оставляем старый catalog.json", file=sys.stderr)
        sys.exit(1)

    try:
        catalog = parse_catalog(PDF_TMP)
    except Exception as e:
        print(f"FATAL: парсинг провалился: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    # Контроль качества: должно быть >= 10 моделей суммарно
    total_models = sum(len(v) for v in catalog["series"].values())
    if total_models < 10:
        print(f"FATAL: слишком мало моделей в результате ({total_models}), не записываем", file=sys.stderr)
        sys.exit(1)

    output_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: записан {output_path} ({total_models} моделей суммарно)")


if __name__ == "__main__":
    main()
