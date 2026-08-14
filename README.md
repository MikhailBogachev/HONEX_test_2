# Avito OEM Parser — HONEX Test Task

Рабочий парсер Avito для поиска автозапчастей по OEM-артикулам.
Использует **Playwright + playwright-stealth** для обхода антибот-защиты.

## Установка

```bash
# 1. Клонировать репозиторий
git clone https://github.com/MikhailBogachev/HONEX_test_2.git
cd parser

# 2. Создать виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Установить браузер Playwright
playwright install chromium
```

## Настройка

По умолчанию парсер использует 10 артикулов и выводит результаты в `output/`.
При необходимости можно переопределить параметры через переменные окружения:

```bash
cp .env.example .env
# Редактировать .env при необходимости
```

## Запуск

```bash
# Обычный запуск в режиме feed (браузер откроется видимым)
python -m src.main

# Режим cards — парсинг карточек напрямую
python -m src.main --mode cards

# Кастомный CSV артикулов
python -m src.main --articles articles.csv

# Headless режим (менее стабилен)
python -m src.main --headless

# Подробный вывод
python -m src.main -v

# Кастомная директория вывода
python -m src.main --output-dir results/

# Многократный запуск (3 прогона подряд, --mode поддерживается)
python -m src.main --runs 3 --mode cards
```

## Режимы парсинга: `feed` и `cards`

Парсер поддерживает два режима, задаваемых через `--mode feed` (по умолчанию) или `--mode cards`.

### `feed` — быстрый (по умолчанию)

Один `page.evaluate()` на странице выдачи Avito: все данные об объявлениях (title, price, URL, seller-превью) извлекаются из DOM ленты. После этого для прошедших фильтр объявлений отдельно загружается профиль продавца (11 полей).

- **Запросов на артикул:** 1 (выдача) + N профилей продавцов
- **seller_type:** `UNKNOWN` — тип продавца определяется только из карточки товара, а не из общей ленты объявлений

### `cards` — детальный

Сначала получает URL-ы со страницы выдачи (`search_article_urls`), затем переходит на **каждую карточку** отдельно и парсит её целиком включая `seller_type`.

- **Запросов на артикул:** 1 (выдача) + N карточек + N профилей
- **seller_type:** определяется напрямую из карточки товара (компания / частное лицо)

**Когда использовать `cards`:** когда обязательно нужен корректный `seller_type`.

```bash
# Быстрый режим (по умолчанию)
python -m src.main --mode feed

# Детальный режим — полные данные из каждой карточки
python -m src.main --mode cards
```

## Выходные файлы

После запуска в директории `output/{mode}/` создаются (`{mode}` = `feed` или `cards`):

| Файл | Описание |
|------|----------|
| `avito_results_{mode}_YYYYMMDD-HHMMSS.csv` | Результаты в CSV |
| `avito_results_{mode}_YYYYMMDD-HHMMSS.json` | Результаты в JSON |
| `avito_results_{mode}_YYYYMMDD-HHMMSS.xlsx` | Результаты + Run summary лист |
| `run_summary_{mode}.json` | Метрики производительности |
| `multi_run_summary_{mode}.json` | Агрегированные метрики (при `--runs N`) |

## Тесты

```bash
# Запуск всех тестов
cd parser
python -m pytest tests/ -v

# Только тесты фильтров
python -m pytest tests/test_filters.py -v

# Проверка compileall
python -m compileall src/
```

## Архитектура

```
src/
├── main.py                # CLI entry point, run(), multi_run()
├── config.py              # Настройки, allowlist доменов, артикулы
├── models.py              # Pydantic dataclasses (Listing, Seller, RunSummary)
├── articles.py            # Загрузка артикулов из CSV
├── domain_allowlist.py    # Проверка доменов Avito
├── __main__.py            # Поддержка python -m src.main
│
├── parsers/               # Парсинг данных с Avito
│   ├── seller_utils.py    # Общие утилиты: seller-id, TreeWalker, тип, даты
│   ├── listing_parser.py  # Парсинг страницы выдачи (feed)
│   ├── card_parser.py     # Парсинг карточки объявления (cards)
│   └── seller_parser.py   # Парсинг профиля продавца (11 полей)
│
├── pipeline/              # Обработка артикулов
│   ├── article_processor.py  # Единый pipeline: feed + cards → filter → enrich
│   ├── browser.py         # Playwright + stealth + delays
│   ├── filters.py         # Бизнес-фильтры (OEM, состояние, регион, цена)
│   └── metrics.py         # Метрики запуска, articles/hour
│
└── output/                # Запись результатов
    ├── output.py          # CSV / JSON / XLSX writers
    ├── dedup.py           # Дедупликация по (article, avito_id)
    └── seller_cache.py    # Run-scoped cache с asyncio.Lock
```

**Принцип разделения:** `parsers/` извлекает данные со страниц, `pipeline/` управляет потоком обработки и фильтрации, `output/` записывает результаты. Общие утилиты парсинга вынесены в [`seller_utils.py`](src/parsers/seller_utils.py).

## Стратегия доступа

Парсер использует **только Playwright** (без мобильного API):

1. `headless=False` — стабильнее обходит детекцию
2. `playwright-stealth` — маскировка `navigator.webdriver` и отпечатков
3. Случайные задержки 2–4 сек между запросами
4. Эмуляция скроллинга перед парсингом
5. При CAPTCHA — пауза с приглашением решить вручную

## Использование AI

- Claude (Anthropic) использовался для генерации частей кода парсера, тестов и документации
- Примерная доля AI-assisted работы: ~55%
- Результат проверялся: синтаксис Python, консистентность типов, логика фильтров, запуск тестов

**Платных сервисов не используется.** Для работы достаточно собственного IP и сессии.

## Summary контрольных запусков

10 артикулов HONEX, 3 прогона каждым режимом. Результаты из файлов `multi_run_summary_*.json`.

### `feed` (3 прогона)

```
Run               Elapsed   Art/hr  Succ art/hr  Success%  Scanned  Valid  Requests
20260814-120734   211.9s    169.9   169.9        100.0%    88       50     237
20260814-121117   181.3s    198.6   198.6        100.0%    88       50     237
20260814-121430   221.8s    162.3   162.3        100.0%    88       50     237
─────────────────────────────────────────────────────────────────────────────────────
AVERAGE           205.0s    176.9   176.9        100.0%    88       50     237
```

### `cards` (3 прогона)

```
Run               Elapsed   Art/hr  Succ art/hr  Success%  Scanned  Valid  Requests
20260814-113405   488.1s    73.8    73.8         100.0%    88       50     683
20260814-114224   473.2s    76.1    76.1         100.0%    88       50     687
20260814-115028   473.2s    76.1    76.1         100.0%    88       50     687
─────────────────────────────────────────────────────────────────────────────────────
AVERAGE           478.1s    75.3    75.3         100.0%    88       50     686
```

**Выводы:**
- Оба режима стабильны — 100% success rate во всех 6 прогонах
- `feed` ~2.3× быстрее (177 vs 75 арт/час) — не требует отдельного перехода на карточку
- `cards` использует ~2.9× больше запросов (686 vs 237) — каждая карточка отдельно
- Все продавцы корректно определены, фильтры (OEM, состояние, регион, цена) отработали штатно