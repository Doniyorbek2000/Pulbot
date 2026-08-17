"""Loyiha asosiy ishga tushirish fayli (Root entrypoint)."""

import asyncio
from bot.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
