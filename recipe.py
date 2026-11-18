#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recipe — консольная тулза для поиска по базам рецептов.

При запуске (или по --refresh) сканирует сайты-источники: сайтмапы и
страницы каталогов, собирает ссылки на рецепты и складывает их в локальный
кэш. Дальше — интерактивный поиск:

  * вводишь запрос (например «борщ»);
  * получаешь страницу результатов с нумерованными ссылками;
  * цифра (1–9, 0 = десятый) — открыть рецепт;
  * любая буква — следующая страница, та же буква ещё раз — третья и т.д.;
  * p — предыдущая страница, q — назад к поиску.

Запуск:
    python3 recipe.py                 # интерактивный режим
    python3 recipe.py -q борщ        # одноразовый поиск
    python3 recipe.py --demo          # офлайн-демо со встроенными рецептами
    python3 recipe.py --refresh       # пересканировать базу принудительно

Зависимостей нет — только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser

VERSION = "1.0.0"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 recipe-finder/" + VERSION)
DEFAULT_PAGE_SIZE = 10
FETCH_TIMEOUT = 12

VERBOSE = False


# --------------------------------------------------------------------------- #
# Модель данных
# --------------------------------------------------------------------------- #

@dataclass
class Recipe:
    title: str
    url: str
    site: str
    description: str = ""
    ingredients: list = field(default_factory=list)
    steps: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "site": self.site,
            "description": self.description,
            "ingredients": self.ingredients,
            "steps": self.steps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        return cls(
            title=d.get("title", ""),
            url=d.get("url", ""),
            site=d.get("site", ""),
            description=d.get("description", ""),
            ingredients=list(d.get("ingredients") or []),
            steps=list(d.get("steps") or []),
        )


# --------------------------------------------------------------------------- #
# Источники
# --------------------------------------------------------------------------- #

# Общий стоп-лист для мусорных ссылок (применяется ко всем источникам).
GENERIC_DENY = [
    r"\.(?:jpg|jpeg|png|gif|webp|avif|css|js|mjs|ico|svg|pdf|mp4|webm|zip|gz)$",
    r"^/(?:search|tag|tags|category|categories|catalog|author|authors|page|pages|"
    r"login|signup|register|profile|privacy|terms|about|contacts?|feedback|rss|feed|"
    r"amp|sitemap|video|photo|news|blog)(?:/|$)",
    r"[?&](?:utm_|fbclid|gclid|yclid|igshid)",
    r"^(?:javascript|mailto|tel|data|#)",
]

# Встроенные источники. Ссылки и шаблоны можно менять/добавлять через
# конфиг ~/.config/recipe-finder/config.json или --sources / --add-source.
BUILTIN_SOURCES = {
    "eda.ru": {
        "seeds": ["https://eda.ru/sitemap.xml", "https://eda.ru/recepty"],
        "allow": [r"^/recepty/[^/?#]+/?$"],
        "catalog": [r"^/recepty/?(?:\?.*)?$"],
        "max_pages": 80,
        "max_recipes": 5000,
    },
    "russianfood.com": {
        "seeds": ["https://www.russianfood.com/sitemap.xml",
                  "https://www.russianfood.com/recipes/"],
        "allow": [r"^/recipes/recipe\.php\b.*\brid=\d+", r"^/recipes/[^/?#]+/?$"],
        "catalog": [r"^/recipes/?(?:\?.*)?$", r"^/recipes/bytype/?.*$"],
        "max_pages": 80,
        "max_recipes": 5000,
    },
    "povarenok.ru": {
        "seeds": ["https://www.povarenok.ru/sitemap.xml",
                  "https://www.povarenok.ru/recipes/"],
        "allow": [r"^/recipes/show/\d+/?$", r"^/recipes/[^/?#]+/?$"],
        "catalog": [r"^/recipes/?(?:\?.*)?$"],
        "max_pages": 60,
        "max_recipes": 4000,
    },
    "1000.menu": {
        "seeds": ["https://1000.menu/sitemap.xml", "https://1000.menu/catalog"],
        "allow": [r"^/(?!catalog(?:/|$)|author(?:/|$)|page(?:/|$)|otzyvy(?:/|$)|"
                  r"search(?:/|$)|sitemap(?:/|$)|login(?:/|$))[a-z0-9][a-z0-9-]{5,}/?$"],
        "catalog": [r"^/catalog/?(?:\?.*)?$", r"^/page/\d+/?$"],
        "max_pages": 60,
        "max_recipes": 4000,
    },
    "webspoon.ru": {
        "seeds": ["https://webspoon.ru/sitemap.xml", "https://webspoon.ru/recipes"],
        "allow": [r"^/rec(?:ipe|ipes)/[^/?#]+/?$"],
        "catalog": [r"^/recipes/?(?:\?.*)?$"],
        "max_pages": 60,
        "max_recipes": 4000,
    },
    "allrecipes.com": {
        "seeds": ["https://www.allrecipes.com/sitemap.xml",
                  "https://www.allrecipes.com/recipes/"],
        "allow": [r"^/recipe/\d+/[^/?#]+/?$"],
        "catalog": [r"^/recipes/?(?:\?.*)?$"],
        "max_pages": 60,
        "max_recipes": 4000,
    },
    "bbcgoodfood.com": {
        "seeds": ["https://www.bbcgoodfood.com/sitemap.xml",
                  "https://www.bbcgoodfood.com/recipes"],
        "allow": [r"^/recipes/[^/?#]+/?$"],
        "catalog": [r"^/recipes/?(?:\?.*)?$"],
        "max_pages": 60,
        "max_recipes": 4000,
    },
}


# --------------------------------------------------------------------------- #
# Офлайн-демо (чтобы тулзу можно было потрогать без интернета)
# --------------------------------------------------------------------------- #

DEMO_RECIPES = [
    {
        "title": "Борщ украинский",
        "description": "Классический наваристый борщ на мясном бульоне со сметаной.",
        "ingredients": ["500 г говядины на кости", "2 свёклы", "3 картофелины",
                        "1 морковь", "1 луковица", "300 г капусты",
                        "2 ст. л. томатной пасты", "2 зубчика чеснока",
                        "соль, перец, лавровый лист", "сметана для подачи"],
        "steps": ["Сварите бульон из говядины, снимите пену.",
                  "Свёклу натрите и потушите с томатной пастой и каплей уксуса.",
                  "Обжарьте лук с морковью и добавьте в бульон.",
                  "Положите картофель и капусту, варите 15 минут.",
                  "Добавьте свёклу, чеснок, соль и лавровый лист.",
                  "Дайте настояться 20 минут, подавайте со сметаной."],
    },
    {
        "title": "Оливье",
        "description": "Тот самый новогодний салат с колбасой и горошком.",
        "ingredients": ["3 картофелины", "2 моркови", "3 яйца", "200 г варёной колбасы",
                        "1 банка зелёного горошка", "3 солёных огурца",
                        "1 луковица", "майонез", "соль"],
        "steps": ["Отварите картофель, морковь и яйца до готовности, остудите.",
                  "Нарежьте всё мелким кубиком.",
                  "Добавьте горошек и мелко нарезанный лук.",
                  "Заправьте майонезом, посолите и уберите в холод на час."],
    },
    {
        "title": "Щи из квашеной капусты",
        "description": "Кислые щи на свиных рёбрышках — разогревают и радуют.",
        "ingredients": ["400 г свиных рёбрышек", "300 г квашеной капусты",
                        "3 картофелины", "1 морковь", "1 луковица",
                        "2 ст. л. томатной пасты", "соль, перец, лавровый лист",
                        "сметана"],
        "steps": ["Сварите бульон из рёбрышек.",
                  "Квашеную капусту потушите с томатной пастой 15 минут.",
                  "Добавьте в бульон картофель, затем капусту с обжаренными луком и морковью.",
                  "Варите 10 минут, посолите и дайте настояться.",
                  "Подавайте со сметаной."],
    },
    {
        "title": "Окрошка на квасе",
        "description": "Летний холодный суп с колбасой, яйцом и квасом.",
        "ingredients": ["1 л кваса", "300 г варёной колбасы", "3 картофелины",
                        "3 яйца", "2 свежих огурца", "1 пучок зелёного лука",
                        "редис", "сметана", "соль"],
        "steps": ["Отварите картофель и яйца, остудите.",
                  "Нарежьте колбасу, огурцы, редис, яйца и картофель кубиком.",
                  "Мелко нарежьте зелёный лук, посолите и разотрите.",
                  "Залейте квасом, добавьте сметану и подавайте холодным."],
    },
    {
        "title": "Плов узбекский",
        "description": "Рассыпчатый плов с бараниной, зирой и барбарисом.",
        "ingredients": ["1 кг баранины", "1 кг риса девзира", "3 моркови",
                        "3 луковица", "1 головка чеснока", "зира, барбарис, соль",
                        "растительное масло"],
        "steps": ["Обжарьте мясо до корочки, добавьте лук и морковь.",
                  "Заложите чеснок, специи и залейте водой, тушите 40 минут.",
                  "Рис промойте, выложите сверху, долейте кипяток на 1 см.",
                  "Варите под крышкой на слабом огне 25 минут.",
                  "Перемешайте и дайте настояться 15 минут."],
    },
    {
        "title": "Гречка с грибами и луком",
        "description": "Простой сытный гарнир или постное блюдо.",
        "ingredients": ["1 стакан гречки", "300 г шампиньонов", "1 луковица",
                        "2 ст. л. растительного масла", "соль, перец"],
        "steps": ["Промойте гречку и отварите в подсоленной воде.",
                  "Обжарьте лук, добавьте грибы и жарьте до влаги.",
                  "Соедините с гречкой и прогрейте 5 минут."],
    },
    {
        "title": "Блины на молоке",
        "description": "Тонкие ажурные блины — классика Масленицы.",
        "ingredients": ["500 мл молока", "2 яйца", "1,5 стакана муки",
                        "2 ст. л. сахара", "щепотка соли", "3 ст. л. растительного масла"],
        "steps": ["Взбейте яйца с сахаром и солью.",
                  "Добавьте половину молока и муку, размешайте до однородности.",
                  "Влейте остальное молоко и масло, дайте тесту постоять 20 минут.",
                  "Жарьте на раскалённой сковороде с двух сторон."],
    },
    {
        "title": "Сырники",
        "description": "Нежные творожные сырники с румяной корочкой.",
        "ingredients": ["400 г творога", "1 яйцо", "3 ст. л. муки", "2 ст. л. сахара",
                        "щепотка соли", "ванилин", "растительное масло для жарки"],
        "steps": ["Смешайте творог, яйцо, сахар, соль и ванилин.",
                  "Добавьте муку и замесите мягкое тесто.",
                  "Сформируйте сырники, обваляйте в муке.",
                  "Обжарьте с двух сторон до золотистой корочки."],
    },
    {
        "title": "Паста карбонара",
        "description": "Римская классика: спагетти, гуанчиале, яйцо и пекорино.",
        "ingredients": ["400 г спагетти", "150 г гуанчиале или бекона", "3 желтка",
                        "1 яйцо", "50 г пекорино", "чёрный перец", "соль"],
        "steps": ["Сварите спагетти в подсоленной воде.",
                  "Обжарьте нарезанный гуанчиале до хруста.",
                  "Смешайте желтки с тёртым пекорино и перцем.",
                  "Спагетти смешайте с гуанчиале, снимите с огня и быстро вмешайте яичную смесь.",
                  "Подавайте сразу, посыпав пекорино."],
    },
    {
        "title": "Пицца маргарита",
        "description": "Неаполитанская пицца: помидоры, моцарелла, базилик.",
        "ingredients": ["300 г муки", "200 мл тёплой воды", "7 г сухих дрожжей",
                        "1 ст. л. оливкового масла", "соль", "300 г томатов в собственном соку",
                        "250 г моцареллы", "базилик"],
        "steps": ["Замесите тесто, оставьте на час подниматься.",
                  "Разомните томаты, добавьте соль и масло — это соус.",
                  "Раскатайте тесто, смажьте соусом, выложите моцареллу.",
                  "Выпекайте при максимальной температуре 7–10 минут.",
                  "Украсьте базиликом."],
    },
    {
        "title": "Тирамису",
        "description": "Итальянский десерт с маскарпоне, савоярди и кофе.",
        "ingredients": ["500 г маскарпоне", "4 яйца", "100 г сахара",
                        "300 г печенья савоярди", "300 мл крепкого эспрессо",
                        "какао для посыпки"],
        "steps": ["Взбейте желтки с сахаром, добавьте маскарпоне.",
                  "Взбейте белки до пиков и вмешайте в крем.",
                  "Быстро обмакните печенье в остывший эспрессо.",
                  "Выложите слоями: печенье, крем, печенье, крем.",
                  "Охладите 4 часа и посыпьте какао."],
    },
    {
        "title": "Шарлотка с яблоками",
        "description": "Простой и быстрый яблочный пирог.",
        "ingredients": ["4 яблока", "4 яйца", "1 стакан сахара", "1 стакан муки",
                        "щепотка соли", "сливочное масло для формы"],
        "steps": ["Взбейте яйца с сахаром до пышной пены.",
                  "Постепенно введите муку и соль.",
                  "Форму смажьте маслом, выложите нарезанные яблоки.",
                  "Залейте тестом и выпекайте 30 минут при 180 °C."],
    },
    {
        "title": "Сырный суп с брокколи",
        "description": "Нежный крем-суп с яркой брокколи и сырным вкусом.",
        "ingredients": ["400 г брокколи", "200 г плавленого сыра", "1 картофелина",
                        "1 луковица", "500 мл молока", "соль, мускатный орех"],
        "steps": ["Обжарьте лук, добавьте картофель и брокколи.",
                  "Залейте молоком и варите 15 минут до мягкости.",
                  "Пюрируйте блендером, добавьте сыр и прогрейте.",
                  "Посолите и подавайте с сухариками."],
    },
    {
        "title": "Харчо",
               "description": "Острый грузинский суп с рисом и говядиной.",
        "ingredients": ["500 г говядины", "100 г риса", "1 луковица",
                        "3 ст. л. томатной пасты", "2 зубчика чеснока",
                        "хмели-сунели, зелень, соль"],
        "steps": ["Сварите бульон из говядины, нарежьте мясо.",
                  "Обжарьте лук, добавьте томатную пасту и специи.",
                  "Положите рис в бульон, варите 10 минут.",
                  "Добавьте зажарку и чеснок, посолите и дайте настояться."],
    },
]


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    if VERBOSE:
        print(f"  [debug] {msg}", file=sys.stderr)


def norm_url(url: str) -> str:
    """Приводит ссылку к единому виду: без якоря и рекламных параметров."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme:
        return url.rstrip("/")
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = parts.query
    if query:
        keep = [p for p in query.split("&")
                if p and not re.match(r"^(?:utm_|fbclid|gclid|yclid|igshid|ref|source)=",
                                      p, re.I)]
        query = "&".join(keep)
    return urllib.parse.urlunsplit((parts.scheme.lower(), host, path, query, ""))


def host_of(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_host(url: str, host: str) -> bool:
    return host_of(url) == host


def pathq(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return parts.path + (("?" + parts.query) if parts.query else "")


def matches_any(url: str, regexes) -> bool:
    if not regexes:
        return True
    pq = pathq(url)
    return any(r.search(pq) for r in regexes)


def is_denied(url: str, regexes) -> bool:
    pq = pathq(url).lower()
    return any(r.search(pq) for r in regexes)


def title_from_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return url
    last = segments[-1]
    last = re.sub(r"\.(?:html?|php|aspx?|jsp)$", "", last, flags=re.I)
    last = urllib.parse.unquote(last)
    last = re.sub(r"[-_+]+", " ", last).strip()
    last = re.sub(r"\s+", " ", last)
    return last or url


BAD_TITLES = {"рецепт", "recipe", "подробнее", "читать", "далее", "жми", "смотреть",
              "все рецепты", "recipes", "блюдо", "dish", "..."}


def clean_title(text: str) -> str:
    t = html.unescape(text or "")
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip("«»\"' ")
    return t


def usable_title(text: str) -> str:
    t = clean_title(text)
    if len(t) < 4:
        return ""
    if t.lower() in BAD_TITLES:
        return ""
    return t


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# HTTP и парсинг HTML
# --------------------------------------------------------------------------- #

def fetch(url: str, timeout: int = FETCH_TIMEOUT, retries: int = 2):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    })
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
            if url.lower().split("?")[0].endswith(".gz"):
                data = gzip.decompress(data)
            m = re.search(r'charset=["\']?([\w-]+)', data[:2048].decode("ascii", "ignore"), re.I)
            if m:
                charset = m.group(1)
            return data.decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001 — сеть капризна, глушим всё
            last = exc
            time.sleep(0.3 * (attempt + 1))
    log(f"fetch failed: {url} -> {last}")
    return None


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            d = dict(attrs)
            self._href = d.get("href")
            self._buf = []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._buf)))
            self._href = None
            self._buf = []


class LocParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.locs = []
        self._in = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "loc":
            self._in = True
            self._buf = []

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "loc" and self._in:
            self.locs.append("".join(self._buf).strip())
            self._in = False


def extract_links(text: str, base: str):
    p = LinkParser()
    try:
        p.feed(text)
    except Exception:  # noqa: BLE001
        pass
    out = []
    for href, txt in p.links:
        if not href:
            continue
        href = href.strip()
        if href.startswith("#"):
            continue
        url = urllib.parse.urljoin(base, href)
        if url.startswith(("http://", "https://")):
            out.append((url, txt))
    return out


def parse_locs(text: str):
    p = LocParser()
    try:
        p.feed(text)
    except Exception:  # noqa: BLE001
        pass
    return p.locs


def looks_like_sitemap(text: str, url: str) -> bool:
    if re.search(r"<(?:urlset|sitemapindex)\b", text[:6000], re.I):
        return True
    path = urllib.parse.urlsplit(url).path.lower()
    return path.endswith(".xml") or path.endswith(".xml.gz")


def looks_like_sitemap_url(url: str) -> bool:
    return urllib.parse.urlsplit(url).path.lower().endswith((".xml", ".xml.gz"))


# --------------------------------------------------------------------------- #
# Сканирование источника
# --------------------------------------------------------------------------- #

def crawl_source(src: dict) -> dict:
    """Обходит источник и возвращает {url: title}."""
    name = src["name"]
    seeds = list(src.get("seeds") or [])
    if not seeds:
        return {}
    host = host_of(seeds[0])
    allow = [re.compile(p) for p in src.get("allow") or []]
    deny = [re.compile(p) for p in (GENERIC_DENY + list(src.get("deny") or []))]
    catalog = [re.compile(p) for p in src.get("catalog") or []]
    max_pages = int(src.get("max_pages", 40))
    max_recipes = int(src.get("max_recipes", 2000))
    delay = float(src.get("delay", 0.15))
    timeout = int(src.get("timeout", FETCH_TIMEOUT))

    found: dict = {}
    pages = deque((s, 0) for s in seeds)
    seen_pages = set()
    seen_sm = set()
    sm_fetches = 0

    while pages and len(seen_pages) < max_pages and len(found) < max_recipes:
        url, depth = pages.popleft()
        key = norm_url(url)
        if not key or key in seen_pages:
            continue
        seen_pages.add(key)
        text = fetch(url, timeout=timeout)
        if text is None:
            continue
        if delay:
            time.sleep(delay)

        if looks_like_sitemap(text, url):
            log(f"{name}: sitemap {url}")
            for loc in parse_locs(text):
                n = norm_url(loc)
                if not n or not same_host(n, host) or is_denied(n, deny):
                    continue
                if (looks_like_sitemap_url(n) and depth < 2 and sm_fetches < 20
                        and n not in seen_sm):
                    seen_sm.add(n)
                    sm_fetches += 1
                    pages.append((n, depth + 1))
                elif matches_any(n, allow):
                    found.setdefault(n, title_from_url(n))
        else:
            log(f"{name}: page {url}")
            for href, txt in extract_links(text, base=url):
                n = norm_url(href)
                if not n or not same_host(n, host) or is_denied(n, deny):
                    continue
                if matches_any(n, allow):
                    t = usable_title(txt) or title_from_url(n)
                    if len(t) >= 4:
                        found.setdefault(n, t)
                elif matches_any(n, catalog):
                    pages.append((n, depth))
    log(f"{name}: всего {len(found)} ссылок, {len(seen_pages)} страниц")
    return found


# --------------------------------------------------------------------------- #
# Кэш
# --------------------------------------------------------------------------- #

def cache_dir() -> str:
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        base = xdg
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.path.expanduser("~/.cache")
    d = os.path.join(base, "recipe-finder")
    return d


def cache_path() -> str:
    return os.path.join(cache_dir(), "index.json")


def config_path() -> str:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = xdg
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~/.config")
    return os.path.join(base, "recipe-finder", "config.json")


class Cache:
    def __init__(self, ttl_hours: float):
        self.path = cache_path()
        self.ttl = ttl_hours

    def valid(self) -> bool:
        if self.ttl <= 0:
            return False
        try:
            age = (time.time() - os.path.getmtime(self.path)) / 3600.0
        except OSError:
            return False
        return age < self.ttl

    def load_data(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    def save(self, recipes, sig: str) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": VERSION,
                           "sig": sig,
                           "updated": time.time(),
                           "recipes": [r.to_dict() for r in recipes]},
                          f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            log(f"cache save failed: {exc}")


def sources_sig(sources: list) -> str:
    """Отпечаток набора источников — чтобы кэш не смешивался между запусками."""
    payload = json.dumps(
        [[s.get("name"), sorted(s.get("seeds") or []), sorted(s.get("allow") or [])]
         for s in sources],
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Конфиг и Sources
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def resolve_sources(args) -> list:
    cfg_sources = load_config().get("sources", {}) or {}
    sources = {}

    # встроенные
    names = args.sources.split(",") if args.sources else None
    if names is None:
        names = list(BUILTIN_SOURCES.keys())
    for item in names:
        item = item.strip()
        if not item:
            continue
        if "=" in item and item not in BUILTIN_SOURCES:
            nm, seed = item.split("=", 1)
            sources[nm.strip()] = make_custom_source(nm.strip(), seed.strip())
        elif item in BUILTIN_SOURCES:
            sources[item] = dict(BUILTIN_SOURCES[item], name=item)
        elif item in cfg_sources:
            sources[item] = dict(cfg_sources[item], name=item)
        else:
            print(f"  ! неизвестный источник «{item}» — пропускаю", file=sys.stderr)

    # конфиг (если не задан явный список)
    if args.sources is None:
        for nm, s in cfg_sources.items():
            sources.setdefault(nm, dict(s, name=nm))

    # --add-source name=url
    for item in args.add_source:
        if "=" not in item:
            print(f"  ! --add-source: ожидается name=url, получено «{item}»",
                  file=sys.stderr)
            continue
        nm, seed = item.split("=", 1)
        sources[nm.strip()] = make_custom_source(nm.strip(), seed.strip())

    return list(sources.values())


def make_custom_source(name: str, seed: str) -> dict:
    return {
        "name": name,
        "seeds": [seed],
        "allow": [],          # пусто = принимать все ссылки своего хоста (кроме deny)
        "deny": [],
        "catalog": [],
        "max_pages": 40,
        "max_recipes": 2000,
    }


# --------------------------------------------------------------------------- #
# Построение индекса
# --------------------------------------------------------------------------- #

def build_index(sources: list, args) -> tuple:
    """Возвращает (recipes, meta)."""
    if args.demo:
        return demo_recipes(), {"demo": True}

    cache = Cache(args.cache_ttl)
    sig = sources_sig(sources)
    if cache.valid() and not args.refresh:
        data = cache.load_data()
        if data and data.get("sig") == sig:
            recs = [Recipe.from_dict(r) for r in data.get("recipes", [])]
            if recs:
                age_h = (time.time() - os.path.getmtime(cache.path)) / 3600.0
                when = (f"{int(age_h)} ч назад" if age_h >= 1
                        else f"{int(age_h * 60)} мин назад")
                print(f"База загружена из кэша ({when}). "
                      f"--refresh — пересканировать.")
                return recs, {"cached": True, "per_site": {}}

    recipes, per_site = scrape_all_ordered(sources, args)
    if not recipes:
        print("Не удалось получить рецепты из сети — включаю демо-режим "
              "(встроенные рецепты).")
        return demo_recipes(), {"demo": True}
    cache.save(recipes, sig)
    return recipes, {"cached": False, "per_site": per_site}


def scrape_all_ordered(sources: list, args) -> tuple:
    """Сканирует источники параллельно, но собирает результат по порядку."""
    per_site = {}
    results: dict = {}
    if not sources:
        return [], per_site
    print("Сканирую источники рецептов…")
    workers = max(1, min(4, len(sources)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(crawl_source, s): s for s in sources}
        for fut in concurrent.futures.as_completed(futs):
            s = futs[fut]
            try:
                found = fut.result()
            except Exception as exc:  # noqa: BLE001
                log(f"{s['name']}: ошибка {exc}")
                found = {}
            results[s["name"]] = found
            per_site[s["name"]] = len(found)
            print(f"  {s['name']}: {len(found)} рецептов")

    merged: list = []
    seen = set()
    for s in sources:
        for url, title in (results.get(s["name"]) or {}).items():
            if url in seen:
                continue
            seen.add(url)
            merged.append(Recipe(title=title, url=url, site=s["name"]))
    if args.limit and args.limit > 0:
        merged = merged[:args.limit]
    return merged, per_site


def demo_recipes() -> list:
    out = []
    for d in DEMO_RECIPES:
        out.append(Recipe(title=d["title"], url="", site="demo",
                          description=d.get("description", ""),
                          ingredients=list(d.get("ingredients", [])),
                          steps=list(d.get("steps", []))))
    return out


# --------------------------------------------------------------------------- #
# Поиск
# --------------------------------------------------------------------------- #

def search_recipes(recipes: list, query: str) -> list:
    q = norm_text(query)
    if not q:
        return []
    scored = []
    q_words = [w for w in q.split() if w]
    for r in recipes:
        t = norm_text(r.title)
        if not t:
            continue
        if q == t:
            score = 200
        elif t.startswith(q):
            score = 150
        elif q in t:
            score = 120
        elif q_words and all(w in t for w in q_words):
            score = 100
        else:
            ratio = difflib.SequenceMatcher(None, q, t).ratio()
            score = ratio * 80 if ratio >= 0.6 else 0
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    items = [r for _, r in scored]
    return interleave(items)


def interleave(items: list) -> list:
    """Чередует результаты по сайтам, чтобы на странице были разные источники."""
    groups: dict = {}
    order = []
    for it in items:
        if it.site not in groups:
            groups[it.site] = []
            order.append(it.site)
        groups[it.site].append(it)
    out = []
    while True:
        progressed = False
        for site in order:
            if groups[site]:
                out.append(groups[site].pop(0))
                progressed = True
        if not progressed:
            break
    return out


# --------------------------------------------------------------------------- #
# Разбор страницы рецепта
# --------------------------------------------------------------------------- #

SCRIPT_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)


def walk_objects(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_objects(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_objects(v)


def extract_steps(instr) -> list:
    steps = []
    if isinstance(instr, str):
        return [instr.strip()] if instr.strip() else []
    if isinstance(instr, dict):
        instr = [instr]
    if isinstance(instr, list):
        for it in instr:
            if isinstance(it, str):
                if it.strip():
                    steps.append(it.strip())
            elif isinstance(it, dict):
                if str(it.get("@type", "")).lower() == "howtosection":
                    steps.extend(extract_steps(it.get("itemListElement") or it.get("steps")))
                else:
                    t = it.get("text") or it.get("name")
                    if t and str(t).strip():
                        steps.append(str(t).strip())
    return steps


def normalize_jsonld(obj: dict) -> dict:
    name = obj.get("name") or obj.get("headline") or ""
    desc = obj.get("description") or ""
    ings = obj.get("recipeIngredient") or obj.get("ingredients") or []
    if isinstance(ings, str):
        ings = [ings]
    ingredients = [str(x).strip() for x in ings if str(x).strip()]
    steps = extract_steps(obj.get("recipeInstructions") or obj.get("instructions"))
    return {"name": str(name).strip(), "description": str(desc).strip(),
            "ingredients": ingredients, "steps": steps, "partial": False}


def parse_jsonld(text: str):
    for m in SCRIPT_RE.finditer(text):
        raw = m.group(1).strip()
        data = None
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except Exception:  # noqa: BLE001
                continue
        for obj in walk_objects(data):
            types = obj.get("@type")
            tl = [types] if isinstance(types, str) else list(types or [])
            if any(str(t).lower() == "recipe" for t in tl):
                return normalize_jsonld(obj)
    return None


def parse_meta(text: str) -> dict:
    name = ""
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
                  text, re.I)
    if m:
        name = m.group(1)
    if not name:
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        if m:
            name = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()
            name = re.split(r"\s+[|—–-]\s+", name)[0].strip()
    desc = ""
    m = re.search(r'<meta[^>]*(?:name|property)=["\'](?:description|og:description)["\']'
                  r'[^>]*content=["\']([^"\']+)', text, re.I)
    if m:
        desc = html.unescape(m.group(1)).strip()
    return {"name": name, "description": desc, "ingredients": [], "steps": [],
            "partial": True}


def fetch_details(url: str):
    text = fetch(url)
    if not text:
        return None
    return parse_jsonld(text) or parse_meta(text)


# --------------------------------------------------------------------------- #
# Ввод с клавиатуры (один символ, без Enter, если есть tty)
# --------------------------------------------------------------------------- #

def read_key() -> str:
    """Возвращает один символ. На tty — без нажатия Enter, иначе — из строки."""
    if sys.stdin.isatty():
        try:
            if os.name == "posix":
                import termios
                import tty
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                    ch = sys.stdin.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                return ch
            if os.name == "nt":
                import msvcrt
                return msvcrt.getch().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            pass
    line = sys.stdin.readline()
    return line[:1] if line else ""


def prompt(text: str) -> str:
    try:
        return input(text)
    except EOFError:
        return ""


# --------------------------------------------------------------------------- #
# Вывод
# --------------------------------------------------------------------------- #

def print_help() -> None:
    print("""
┌─ Справка ─────────────────────────────────────────────────────────────┐
│ Поиск:       введи название блюда и Enter                              │
│              q — выход, ? — справка, /stats — статистика базы          │
│ Результаты:  цифра 1–9 (0 = десятый) — открыть рецепт                  │
│              любая буква — следующая страница (ещё раз — третья…)      │
│              p — предыдущая страница, q — новый поиск                  │
│ Карточка:    o — открыть ссылку в браузере, Enter — назад             │
└───────────────────────────────────────────────────────────────────────┘""")


def print_stats(recipes: list) -> None:
    per_site: dict = {}
    for r in recipes:
        per_site[r.site] = per_site.get(r.site, 0) + 1
    print(f"Всего рецептов: {len(recipes)}")
    for site, n in sorted(per_site.items(), key=lambda x: -x[1]):
        print(f"  {site}: {n}")


def print_results_page(results: list, page: int, size: int) -> None:
    total = len(results)
    pages = max(1, (total + size - 1) // size)
    start = page * size
    chunk = results[start:start + size]
    print(f"\nНайдено: {total}   Страница {page + 1}/{pages}")
    print("─" * 70)
    for i, r in enumerate(chunk):
        num = i + 1
        label = "0" if num == 10 else str(num)
        print(f" {label}. [{r.site}] {r.title}")
        if r.url:
            url = r.url if len(r.url) <= 95 else r.url[:92] + "..."
            print(f"     {url}")
    print("─" * 70)
    print("цифра — открыть • буква — след. страница • p — назад • q — к поиску")


def print_card(rec: Recipe, details: dict) -> None:
    line = "─" * 70
    print("\n" + line)
    title = (details.get("name") or rec.title).strip()
    print(f"  {title}")
    print(f"  Источник: {rec.site}")
    if rec.url:
        print(f"  Ссылка:   {rec.url}")
    else:
        print("  Ссылка:   (демо-данные, ссылка отсутствует)")
    desc = details.get("description") or rec.description
    if desc:
        print(f"  {desc[:300]}")
    ingredients = details.get("ingredients") or rec.ingredients
    steps = details.get("steps") or rec.steps
    if ingredients:
        print("  Ингредиенты:")
        for ing in ingredients:
            print(f"   • {ing}")
    if steps:
        print("  Приготовление:")
        for i, st in enumerate(steps, 1):
            print(f"   {i}. {st}")
    if not ingredients and not steps:
        print("  (не удалось разобрать страницу — откройте ссылку в браузере)")
    print(line)
    print("[o] открыть в браузере   [Enter] назад к результатам")


# --------------------------------------------------------------------------- #
# Режимы работы
# --------------------------------------------------------------------------- #

def run_query(recipes: list, args) -> int:
    results = search_recipes(recipes, args.query)
    if args.json:
        out = [{"title": r.title, "url": r.url, "site": r.site}
               for r in results[:50]]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if not results:
        print("Ничего не найдено.")
        return 0
    print(f"Найдено: {len(results)}  (запрос: «{args.query}»)")
    for i, r in enumerate(results[:args.page_size * 3], 1):
        print(f" {i}. [{r.site}] {r.title}")
        print(f"     {r.url or '(без ссылки)'}")
    if args.open and results and results[0].url:
        print(f"Открываю: {results[0].url}")
        webbrowser.open(results[0].url)
    return 0


def run_interactive(recipes: list, args) -> int:
    size = args.page_size
    print(f"В базе {len(recipes)} рецептов. Введи запрос, ? — справка, q — выход.")
    while True:
        query = prompt("\nПоиск> ")
        if query == "":
            continue
        low = query.strip().lower()
        if low in ("q", "exit", "quit", "выход"):
            print("Пока!")
            return 0
        if low == "?":
            print_help()
            continue
        if low in ("/stats", "stats", "статистика"):
            print_stats(recipes)
            continue

        results = search_recipes(recipes, query)
        if not results:
            print("Ничего не найдено. Попробуй другое название.")
            continue

        page = 0
        while True:
            print_results_page(results, page, size)
            key = read_key()
            if key == "":
                # EOF / Enter без символа — выходим к поиску
                break
            k = key.lower()
            if k in "0123456789":
                idx = page * size + (9 if key == "0" else int(key) - 1)
                if idx >= len(results):
                    print("Такого пункта нет на странице.")
                    continue
                rec = results[idx]
                details = None
                if rec.url:
                    print("  Загружаю страницу рецепта…")
                    try:
                        details = fetch_details(rec.url)
                    except Exception as exc:  # noqa: BLE001
                        log(f"details failed: {exc}")
                        details = None
                print_card(rec, details or {})
                # клавиши карточки
                while True:
                    k2 = read_key()
                    if k2 == "":
                        break
                    if k2.lower() == "o" and rec.url:
                        print(f"  Открываю {rec.url}")
                        try:
                            webbrowser.open(rec.url)
                        except Exception as exc:  # noqa: BLE001
                            print(f"  Не удалось открыть браузер: {exc}")
                    break
                continue  # перерисовать ту же страницу результатов
            if key in ("\r", "\n"):
                continue  # Enter — просто перерисовать страницу
            if k == "p":
                if page > 0:
                    page -= 1
                else:
                    print("Это первая страница.")
                continue
            if k == "q":
                break  # назад к поиску
            # любая другая буква — следующая страница
            if (page + 1) * size < len(results):
                page += 1
            else:
                print("Это последняя страница.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="recipe",
        description="Парсер баз рецептов и интерактивный поиск по ним.",
        epilog="Примеры:\n"
               "  python3 recipe.py\n"
               "  python3 recipe.py -q борщ\n"
               "  python3 recipe.py --demo\n"
               "  python3 recipe.py --refresh --sources eda.ru,russianfood.com\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-q", "--query", metavar="TEXT",
                   help="одноразовый поиск (без интерактива)")
    p.add_argument("--demo", "--offline", dest="demo", action="store_true",
                   help="офлайн-демо со встроенными рецептами")
    p.add_argument("-r", "--refresh", "--update", dest="refresh",
                   action="store_true",
                   help="пересканировать источники, игнорируя кэш")
    p.add_argument("--sources", metavar="LIST",
                   help="источники через запятую (имена встроенных или name=url)")
    p.add_argument("--add-source", action="append", default=[], metavar="NAME=URL",
                   help="добавить свой источник (можно несколько раз)")
    p.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE,
                   help=f"рецептов на странице (по умолчанию {DEFAULT_PAGE_SIZE})")
    p.add_argument("--limit", type=int, default=0,
                   help="максимум рецептов в базе (0 — без ограничения)")
    p.add_argument("--cache-ttl", type=float, default=24.0,
                   help="сколько часов считать кэш свежим (по умолчанию 24)")
    p.add_argument("--json", action="store_true",
                   help="с --query: вывод в JSON")
    p.add_argument("--open", action="store_true",
                   help="с --query: открыть первый результат в браузере")
    p.add_argument("--list-sources", action="store_true",
                   help="показать встроенные источники и выйти")
    p.add_argument("--stats", action="store_true",
                   help="показать статистику базы и выйти")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="отладочный вывод")
    p.add_argument("--version", action="version",
                   version=f"recipe {VERSION}")
    return p


def main(argv=None) -> int:
    global VERBOSE
    args = build_parser().parse_args(argv)
    VERBOSE = args.verbose
    if not (1 <= args.page_size <= 20):
        print(" --page-size должен быть в пределах 1..20", file=sys.stderr)
        return 2

    if args.list_sources:
        print("Встроенные источники:")
        for name, s in BUILTIN_SOURCES.items():
            print(f"  {name}: {', '.join(s['seeds'])}")
        print("\nСвои источники: --add-source name=https://сайт/sitemap.xml")
        print(f"Конфиг: {config_path()}")
        return 0

    sources = resolve_sources(args)
    recipes, meta = build_index(sources, args)

    if args.stats:
        print_stats(recipes)
        return 0

    if not meta.get("cached"):
        print(f"Всего в базе: {len(recipes)}")
        if meta.get("per_site"):
            for site, n in sorted(meta["per_site"].items(), key=lambda x: -x[1]):
                print(f"  {site}: {n}")

    if args.query is not None:
        return run_query(recipes, args)
    return run_interactive(recipes, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПока!")
        sys.exit(0)
