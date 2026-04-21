# Mirtorg Reviews Feed

Автоматический генератор фида отзывов для Google Merchant Center (Product Reviews v2.3).

Парсит отзывы с [mirtorg7km.com.ua/ua/testimonials](https://mirtorg7km.com.ua/ua/testimonials),
матчит с товарным фидом GMC и публикует XML через GitHub Pages.

## Как работает

1. GitHub Actions запускается каждый день в 05:00 по Киеву
2. Скрипт собирает все отзывы с текстом (без пустых и дублей)
3. Матчит каждый товар из отзыва с товарным фидом GMC по Prom ID
4. Генерирует XML в формате Product Reviews v2.3
5. Публикует на GitHub Pages

## URL фида (подключить в GMC)

```
https://<your-username>.github.io/<repo-name>/mirtorg_reviews_feed.xml
```

## Локальный запуск

```bash
pip3 install -r requirements.txt

python3 mirtorg_reviews_feed.py --debug    # тест: 1 страница
python3 mirtorg_reviews_feed.py --pages 5  # первые 5 страниц
python3 mirtorg_reviews_feed.py            # полный прогон
```

## Ручной запуск на GitHub

Actions → Generate Mirtorg Reviews Feed → Run workflow
