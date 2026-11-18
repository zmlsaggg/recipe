# Recipe Finder

<p align="center">
  Собери локальный каталог рецептов из нескольких источников и находи нужные блюда через быстрый интерактивный поиск.
</p>

<p align="center">
  <a href="https://pypi.org/project/recipe-finder/"><img src="https://img.shields.io/pypi/v/recipe-finder?style=flat-square&logo=pypi&logoColor=white&label=PyPI&color=F59E0B" alt="PyPI version"></a>
  <a href="https://github.com/zmlsaggg/recipe/releases/latest"><img src="https://img.shields.io/github/v/release/zmlsaggg/recipe?style=flat-square&logo=github&label=release&color=D97706" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.9+">
  <a href="https://github.com/zmlsaggg/recipe/releases/latest"><img src="https://img.shields.io/badge/Windows-EXE-0078D4?style=flat-square&logo=windows&logoColor=white" alt="Windows executable"></a>
</p>

## Установка через pip

Нужен Python 3.9 или новее. Пакет называется `recipe-finder`, а после установки запускается командой `recipe-finder`. Дополнительные Python-зависимости не нужны.

### Windows (PowerShell)

```powershell
py -m pip install --upgrade recipe-finder
recipe-finder
```

### Linux и macOS

```sh
python3 -m pip install --upgrade recipe-finder
recipe-finder
```

Если система не разрешает установку в системный Python, используй отдельное виртуальное окружение:

```powershell
# Windows
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```sh
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

После активации окружения:

```sh
python -m pip install --upgrade recipe-finder
recipe-finder -q борщ
```

Обновить пакет: `python -m pip install --upgrade recipe-finder`. Удалить: `python -m pip uninstall recipe-finder`.
Для изолированной установки CLI можно использовать `pipx install recipe-finder`.

## Запуск из исходников


Консольная тулза, которая **при запуске парсит базы рецептов** с нескольких
сайтов (сайтмапы и страницы каталогов), складывает ссылки в локальный кэш и
даёт **интерактивный поиск** по собранной базе.

Зависимостей нет — только стандартная библиотека Python 3 (проверено на 3.11).

## Быстрый старт

```bash
python3 recipe.py            # интерактивный режим
python3 recipe.py --demo     # офлайн-демо со встроенными рецептами
python3 recipe.py -q борщ    # одноразовый поиск без интерактива
```

При первом запуске тулза сканирует источники (по умолчанию 7 сайтов), пишет
результат в кэш `~/.cache/recipe-finder/index.json` и потом работает с ним.
Повторные запуски берут кэш (по умолчанию он свежий 24 часа), чтобы не
сканировать всё заново. Принудительно пересканировать:

```bash
python3 recipe.py --refresh          # или --update
python3 recipe.py --cache-ttl 0      # вообще не считать кэш свежим
```

## Как пользоваться поиском

```
Поиск> борщ
```

Печатается страница результатов (по умолчанию 10 штук, на странице видно
сайт-источник и ссылку):

```
Найдено: 42   Страница 1/5
──────────────────────────────────────────────────────────────────────
 1. [eda.ru] Борщ классический
     https://eda.ru/recepty/borshch-klassicheskij
 2. [russianfood.com] Борщ украинский
     https://www.russianfood.com/recipes/recipe.php?rid=12345
 ...
──────────────────────────────────────────────────────────────────────
цифра — открыть • буква — след. страница • p — назад • q — к поиску
```

Управление:

| Клавиша | Действие |
|---|---|
| `1`–`9`, `0` (`0` = десятый) | открыть рецепт под этим номером |
| **любая буква** | следующая страница (ещё раз — третья и т.д.) |
| `p` | предыдущая страница |
| `q` | назад к поиску |
| `Enter` | перерисовать страницу |
| `?` | справка |

В карточке рецепта: `o` — открыть ссылку в браузере, `Enter` — назад к списку.
Карточка показывает название, источник, ссылку, ингредиенты и шаги
(берутся из JSON-LD `schema.org/Recipe` на странице; если страницу разобрать
не удалось — печатается просто ссылка).

Результаты чередуются по сайтам, чтобы на одной странице были варианты из разных
источников.

## Флаги

```
-q, --query TEXT        одноразовый поиск (печатает результат и выходит)
--json                  с --query: выдать JSON
--open                  с --query: открыть первый результат в браузере
--demo, --offline       офлайн-демо со встроенными рецептами
-r, --refresh           пересканировать источники, игнорируя кэш
--sources LIST          источники через запятую (имена встроенных или name=url)
--add-source NAME=URL   добавить свой источник (можно несколько раз)
--page-size N           рецептов на странице (по умолчанию 10)
--limit N               максимум рецептов в базе (0 — без ограничения)
--cache-ttl HOURS       сколько часов считать кэш свежим (по умолчанию 24)
--list-sources          показать встроенные источники
--stats                 показать статистику базы
--deep                  глубокий обход (поднять лимиты страниц и рецептов)
--export FILE           сохранить базу в JSON или CSV (по расширению) и выйти
-v, --verbose           отладочный вывод
```

## Источники

Встроены 9 источников: `eda.ru`, `russianfood.com`, `povarenok.ru`, `1000.menu`,
`webspoon.ru`, `gotovim-doma.ru`, `gastronom.ru`, `allrecipes.com`,
`bbcgoodfood.com`.
Посмотреть: `python3 recipe.py --list-sources`.

Прокачанный парсер:

* **Транслитерация в поиске** — запрос `borsch` находит «Борщ», `olivie` — «Оливье».
* **Запасной разбор страницы** — если на странице нет JSON-LD, тулза ищет
  заголовки «Ингредиенты»/«Приготовление» и ближайшие списки после них.
* **Кэш карточек** — разобранные рецепты сохраняются в
  `~/.cache/recipe-finder/details/`, повторный просмотр мгновенный и офлайн.
* **Авто-пагинация каталогов** — ссылки `?page=2`, `/page/2`, «Дальше», «Ещё»,
  `»` обходятся сами, даже если не заданы в `catalog`.
* **`--deep`** — глубокий обход: лимиты страниц/рецептов поднимаются в разы.
* **`--export base.json|base.csv`** — выгрузить собранную базу.

Свои источники можно добавить прямо из командной строки:

```bash
python3 recipe.py --sources moj-sait=https://example.com/sitemap.xml
python3 recipe.py --add-source moj=https://example.com/sitemap.xml
```

Или через конфиг `~/.config/recipe-finder/config.json`, где можно задать
шаблоны ссылок и лимиты:

```json
{
  "sources": {
    "moj-sait": {
      "seeds": ["https://example.com/sitemap.xml", "https://example.com/recipes"],
      "allow": ["^/recipes/[^/?#]+/?$"],
      "catalog": ["^/recipes/?(?:\\?.*)?$"],
      "max_pages": 50,
      "max_recipes": 3000
    }
  }
}
```

`allow` — регулярки для ссылок на рецепты, `catalog` — для страниц-каталогов
(их тулза обходит дополнительно). Если `allow` не задан, берутся все ссылки
своего сайта, кроме мусорных (картинки, теги, авторы и т.п.).

## Как это работает

1. Для каждого источника обходятся сайтмапы (`sitemap.xml`, включая
   вложенные индексы) и страницы каталогов с пагинацией.
2. Из страниц вытаскиваются ссылки на рецепты по шаблонам источника.
3. Всё складывается в кэш `~/.cache/recipe-finder/index.json`
   (с отпечатком набора источников — кэш не смешивается между разными
   наборами сайтов).
4. Поиск — регистронезависимый, `ё`≈`е`; сначала совпадения по подстроке,
   если их нет — нечёткое совпадение (difflib).
5. Карточка рецепта разбирается из JSON-LD на странице (название,
   ингредиенты, шаги), с запасным вариантом через `<title>`/meta-описание.

## Заметки

* Тулза вежливо обходит сайты: задержка между запросами к одному источнику
  (~0.15 с), настраивается полем `delay` в конфиге.
* Если сеть недоступна и база не собралась, автоматически включается демо-режим
  со встроенными рецептами, чтобы тулзой можно было пользоваться офлайн.
* Сетевые источники могут менять верстку — шаблоны ссылок при необходимости
  правятся в `BUILTIN_SOURCES` в `recipe.py` или в конфиге.
