"""Oddiy, tashqi bog'liqliksiz i18n (uz / ru / en).

Matnlar `bot/locales/<lang>.json` da ichma-ich (nested) JSON ko'rinishida
saqlanadi, kalitlar nuqta bilan ajratiladi: `wallet.title`.
Tarjima topilmasa standart tilga, u ham topilmasa kalitning o'ziga tushadi —
shu tufayli yangi kalit qo'shilganda bot ishdan chiqmaydi.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from bot.config import settings

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).parent / "locales"

SUPPORTED_LANGUAGES = ("uz", "ru", "en")

LANGUAGE_NAMES = {
    "uz": "🇺🇿 O'zbekcha",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
}

_catalog: dict[str, dict[str, Any]] = {}


def load_locales() -> None:
    """Barcha til fayllarini xotiraga yuklaydi."""
    _catalog.clear()
    for lang in SUPPORTED_LANGUAGES:
        path = LOCALES_DIR / f"{lang}.json"
        if not path.exists():
            logger.warning("Til fayli topilmadi: %s", path)
            _catalog[lang] = {}
            continue
        with path.open(encoding="utf-8") as fp:
            _catalog[lang] = json.load(fp)
    logger.info("Tillar yuklandi: %s", ", ".join(SUPPORTED_LANGUAGES))


def normalize(language: str | None) -> str:
    """Telegram'dan kelgan til kodini qo'llab-quvvatlanadiganiga moslashtiradi."""
    if not language:
        return settings.default_language
    code = language.lower().replace("_", "-").split("-")[0]
    if code in SUPPORTED_LANGUAGES:
        return code
    # O'zbekistonda ruscha interfeys keng tarqalgan
    if code in ("kk", "ky", "tg", "tt", "be", "uk"):
        return "ru"
    return settings.default_language


def _lookup(tree: dict[str, Any], key: str) -> Any:
    node: Any = tree
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def get(key: str, lang: str = "uz", /, **kwargs: Any) -> str:
    """Tarjimani oladi va `{...}` o'rinbosarlarini to'ldiradi."""
    if not _catalog:
        load_locales()
    lang = normalize(lang)

    value = _lookup(_catalog.get(lang, {}), key)
    if value is None and lang != settings.default_language:
        value = _lookup(_catalog.get(settings.default_language, {}), key)
    if value is None:
        value = _lookup(_catalog.get("en", {}), key)
    if value is None:
        logger.debug("Tarjima yo'q: %s (%s)", key, lang)
        return key

    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    text = str(value)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            logger.warning("Tarjimani formatlashda xato: %s (%s)", key, lang)
    return text


class Translator:
    """Bitta til uchun bog'langan chaqiriladigan obyekt: `_("wallet.title")`."""

    __slots__ = ("lang",)

    def __init__(self, lang: str) -> None:
        self.lang = normalize(lang)

    def __call__(self, key: str, **kwargs: Any) -> str:
        return get(key, self.lang, **kwargs)

    def has(self, key: str) -> bool:
        return _lookup(_catalog.get(self.lang, {}), key) is not None
