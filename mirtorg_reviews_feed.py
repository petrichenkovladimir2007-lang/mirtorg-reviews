#!/usr/bin/env python3
"""
Prom.ua Reviews Parser — mirtorg7km.com.ua
Google Merchant Center Product Reviews Feed v2.3

Запуск:
  python3 mirtorg_reviews_feed.py             # полный прогон
  python3 mirtorg_reviews_feed.py --debug     # 1 страница, подробный лог
  python3 mirtorg_reviews_feed.py --pages 5   # первые N страниц
  python3 mirtorg_reviews_feed.py --output /path/file.xml
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import hashlib
import argparse
from xml.etree import ElementTree as ET
from datetime import timezone, timedelta
from pathlib import Path

# ── Конфигурация ─────────────────────────────────────────────────────────────
CONFIG = {
    "base_url":         "https://mirtorg7km.com.ua",
    "testimonials_url": "https://mirtorg7km.com.ua/ua/testimonials",
    "product_feed_url": (
        "https://mirtorg7km.com.ua/google_merchant_center.xml"
        "?hash_tag=0914cd5c40b4d7dfebbc8e06c96a5afd"
        "&product_ids=&label_ids=&export_lang=ru&group_ids="
    ),
    "publisher_name":   "mirtorg7km.com.ua",
    "publisher_favicon":"https://images.prom.ua/favicon.png",
    "output_file":      "./public/mirtorg_reviews_feed.xml",
    "request_delay":    1.0,
    "max_pages":        None,   # None = автодетект из пагинатора
}

KYIV_TZ = timezone(timedelta(hours=3))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8",
}

# ── Загрузка страниц ──────────────────────────────────────────────────────────
def fetch_bytes(url):
    """Загружает URL, возвращает bytes. Проверяет редирект пагинатора."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [WARN] Ошибка {url}: {e}")
        return None

    # Защита: Prom.ua редиректит несуществующие /page_N на главную страницу
    if "/page_" in url and "/page_" not in resp.url:
        print(f"  [INFO] Редирект — пагинация завершена")
        return None

    return resp.content


# ── Загрузка товарного фида GMC ───────────────────────────────────────────────
def load_product_feed(url):
    """
    Возвращает dict: {prom_id (str) -> {name, url, brand}}
    Читает g:mpn как prom_id (совпадает с числом в URL товара).
    """
    print(f"Загружаю товарный фид: {url}")
    data = fetch_bytes(url)
    if not data:
        raise RuntimeError("Не удалось загрузить товарный фид GMC")

    soup = BeautifulSoup(data, "lxml-xml")
    products = {}

    for item in soup.find_all("item"):
        mpn_tag   = item.find("g:mpn")
        title_tag = item.find("g:title") or item.find("title")
        link_tag  = item.find("g:ads_redirect") or item.find("g:link") or item.find("link")
        brand_tag = item.find("g:brand")

        if not mpn_tag:
            continue

        prom_id = mpn_tag.get_text(strip=True)
        products[prom_id] = {
            "name":  title_tag.get_text(strip=True) if title_tag else "",
            "url":   link_tag.get_text(strip=True)  if link_tag  else "",
            "brand": brand_tag.get_text(strip=True) if brand_tag else "",
        }

    print(f"  Товаров в фиде: {len(products)}")
    return products


# ── Парсинг страницы отзывов ──────────────────────────────────────────────────
def get_total_pages(html_bytes):
    soup = BeautifulSoup(html_bytes, "lxml")
    pag = soup.select_one("[data-pagination-pages-count]")
    if pag:
        try:
            return int(pag["data-pagination-pages-count"])
        except (ValueError, KeyError):
            pass
    return 1


def parse_reviews_page(html_bytes, debug=False):
    """Парсит одну страницу отзывов. Возвращает список dict."""
    soup = BeautifulSoup(html_bytes, "lxml")
    reviews = []

    for item in soup.select("li.b-comments__item"):
        author_el   = item.select_one('[data-qaid="author_name"]')
        date_el     = item.select_one('[data-qaid="review_date"]')
        text_el     = item.select_one('[data-qaid="review_text"]')
        products_el = item.select_one('[data-reviews-products]')
        rating_el   = item.select_one('.b-rating__state')

        if not author_el or not date_el:
            continue

        author = author_el.get_text(strip=True)
        if author == "Коментар продавця":
            continue

        dt   = date_el.get("datetime", "")
        text = text_el.get_text(strip=True) if text_el else ""

        # Рейтинг: "Рейтинг 5 з 5" → 5
        rating = 5
        if rating_el:
            title = rating_el.get("title", "")
            for part in title.split():
                if part.isdigit():
                    rating = int(part)
                    break

        # Теги (добавляем к тексту только если есть основной текст)
        tags = [
            tag.get("data-tag-title", "")
            for tag in item.select("[data-tag-title]")
            if tag.get("data-tag-title")
        ]

        products = []
        if products_el:
            try:
                products = json.loads(
                    products_el.get("data-reviews-products", "[]")
                )
            except (json.JSONDecodeError, TypeError):
                pass

        review = {
            "author":   author,
            "datetime": dt,
            "text":     text,
            "tags":     tags,
            "rating":   rating,
            "products": products,
        }
        reviews.append(review)

        if debug:
            has_text = "✓ текст" if text else "✗ нет текста"
            print(f"    {author} | {dt[:10]} | {has_text} | товаров: {len(products)}")

    return reviews


# ── Генерация XML ─────────────────────────────────────────────────────────────
def make_review_id(author, dt, prom_id):
    """Стабильный MD5-хеш как review_id."""
    raw = f"{author}|{dt}|{prom_id}"
    return "MR" + hashlib.md5(raw.encode()).hexdigest()[:6].upper()


def build_xml(reviews_with_products):
    """
    reviews_with_products: список (review_dict, product_dict, prom_id)
    Возвращает XML-строку в кодировке UTF-8.
    """
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    xsd_url = (
        "http://www.google.com/shopping/reviews/schema/product/2.3/"
        "product_reviews.xsd"
    )

    feed = ET.Element("feed", {
        f"{{http://www.w3.org/2001/XMLSchema-instance}}noNamespaceSchemaLocation": xsd_url
    })

    ET.SubElement(feed, "version").text = "2.3"

    aggregator = ET.SubElement(feed, "aggregator")
    ET.SubElement(aggregator, "name").text = "prom.ua"

    publisher = ET.SubElement(feed, "publisher")
    ET.SubElement(publisher, "name").text = CONFIG["publisher_name"]
    ET.SubElement(publisher, "favicon").text = CONFIG["publisher_favicon"]

    reviews_el = ET.SubElement(feed, "reviews")

    for rv, prod, prom_id in reviews_with_products:
        review_el = ET.SubElement(reviews_el, "review")

        ET.SubElement(review_el, "review_id").text = make_review_id(
            rv["author"], rv["datetime"], prom_id
        )

        reviewer = ET.SubElement(review_el, "reviewer")
        ET.SubElement(reviewer, "name").text = rv["author"]

        # Timestamp: добавляем +03:00 если нет таймзоны
        ts = rv["datetime"]
        if ts and "+" not in ts and "Z" not in ts:
            ts += "+03:00"
        ET.SubElement(review_el, "review_timestamp").text = ts

        # Контент: только текст отзыва (теги Prom.ua не включаем — GMC отклоняет как boilerplate)
        ET.SubElement(review_el, "content").text = rv["text"]

        ET.SubElement(review_el, "review_url", {
            "type": "group"
        }).text = CONFIG["testimonials_url"]

        ratings = ET.SubElement(review_el, "ratings")
        ET.SubElement(ratings, "overall", {
            "min": "1", "max": "5"
        }).text = str(rv["rating"])

        products_el = ET.SubElement(review_el, "products")
        product_el  = ET.SubElement(products_el, "product")

        product_ids = ET.SubElement(product_el, "product_ids")
        mpns = ET.SubElement(product_ids, "mpns")
        ET.SubElement(mpns, "mpn").text = prom_id

        brands = ET.SubElement(product_ids, "brands")
        ET.SubElement(brands, "brand").text = prod.get("brand") or CONFIG["publisher_name"]

        ET.SubElement(product_el, "product_name").text = prod["name"]
        ET.SubElement(product_el, "product_url").text  = prod["url"]

    ET.indent(feed, space="  ")
    tree = ET.ElementTree(feed)

    import io
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


# ── Основной поток ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Prom.ua Reviews → GMC XML Feed v2.3 (mirtorg7km.com.ua)"
    )
    parser.add_argument("--pages",  type=int, default=None,
                        help="Сколько страниц парсить (по умолчанию — все)")
    parser.add_argument("--debug",  action="store_true",
                        help="Режим отладки: 1 страница + детальный лог")
    parser.add_argument("--output", type=str, default=None,
                        help="Путь к выходному файлу (по умолчанию из CONFIG)")
    args = parser.parse_args()

    debug      = args.debug
    output_path = Path(args.output or CONFIG["output_file"])
    max_pages  = 1 if debug else (args.pages or CONFIG["max_pages"])

    print("=" * 60)
    print("Mirtorg7km Reviews Feed Generator")
    print("=" * 60)

    # 1. Загружаем товарный фид
    feed_products = load_product_feed(CONFIG["product_feed_url"])

    # 2. Первая страница отзывов
    print(f"\nЗагружаю первую страницу отзывов...")
    first_html = fetch_bytes(CONFIG["testimonials_url"])
    if not first_html:
        print("Ошибка: не удалось загрузить страницу отзывов.")
        return

    total_pages = get_total_pages(first_html)
    if max_pages:
        total_pages = min(total_pages, max_pages)
    print(f"Страниц для обхода: {total_pages}")

    all_reviews = []

    if debug:
        print("\n[DEBUG] Страница 1:")
    page1 = parse_reviews_page(first_html, debug=debug)
    all_reviews.extend(page1)
    if not debug:
        print(f"Страница 1: {len(page1)} отзывов")

    for page_num in range(2, total_pages + 1):
        url  = f"{CONFIG['testimonials_url']}/page_{page_num}"
        html = fetch_bytes(url)
        if html is None:
            break

        if debug:
            print(f"\n[DEBUG] Страница {page_num}:")
        revs = parse_reviews_page(html, debug=debug)
        if not revs:
            print(f"Страница {page_num}: пусто — стоп")
            break

        all_reviews.extend(revs)
        if not debug and page_num % 10 == 0:
            print(f"Страница {page_num}/{total_pages}: собрано {len(all_reviews)}")

        time.sleep(CONFIG["request_delay"])

    print(f"\nВсего отзывов собрано: {len(all_reviews)}")

    # 3. Фильтр: только уникальные с текстом (ключ author+datetime)
    unique_with_text = {}
    for r in all_reviews:
        if r["text"]:
            key = (r["author"], r["datetime"])
            if key not in unique_with_text:
                unique_with_text[key] = r

    print(f"Уникальных с текстом:  {len(unique_with_text)}")

    # 4. Матчинг с товарным фидом
    matched = []
    unmatched_count = 0

    for rv in unique_with_text.values():
        for prod in rv["products"]:
            prom_id = str(prod["id"])
            if prom_id in feed_products:
                matched.append((rv, feed_products[prom_id], prom_id))
            else:
                unmatched_count += 1

    print(f"Строк в фиде (с матчем): {len(matched)}")
    print(f"Товаров без матча:        {unmatched_count}")

    if not matched:
        print("\n[WARN] Нет ни одного совпадения с товарным фидом!")
        print("  Проверьте URL фида или запустите --debug для диагностики")
        return

    # 5. Генерация XML
    xml_bytes = build_xml(matched)

    # 6. Сохранение
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(xml_bytes)

    size_kb = output_path.stat().st_size / 1024
    print(f"\nФид сохранён: {output_path}")
    print(f"Размер: {size_kb:.1f} KB  |  Отзывов в фиде: {len(matched)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
