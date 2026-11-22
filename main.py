import asyncio
import os
import socket
import traceback
import uvicorn
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

from database import engine, Base, AsyncSessionLocal
from services import seed_initial_data
from bot import dp, bot, router
from admin_crud import admin_crud_router
from admin_dashboard_pro import admin_pro_router
from student_resources import student_res_router

from bulk_handlers import bulk_router
from admin_handlers import admin_router
from ai_handlers import ai_router
from tools_handlers import tools_router
from api import app

_ROUTERS_REGISTERED = False


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await seed_initial_data(session)


def _register_routers():
    global _ROUTERS_REGISTERED
    if _ROUTERS_REGISTERED:
        return
    dp.include_router(admin_pro_router)
    dp.include_router(admin_crud_router)
    dp.include_router(bulk_router)
    dp.include_router(admin_router)
    dp.include_router(ai_router)
    dp.include_router(tools_router)
    dp.include_router(router)
    dp.include_router(student_res_router)
    _ROUTERS_REGISTERED = True


@dp.error()
async def on_dispatcher_error(event):
    print("[Dispatcher Exception]:", event.exception)
    traceback.print_exc()
    return True


async def run_bot_with_retry():
    _register_routers()
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET
    bot.session = session

    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
            break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[Telegram Polling Error]: {exc} — retrying in 5s...")
            await asyncio.sleep(5)


async def start_services():
    await init_db()
    _register_routers()

    port = int(os.getenv("PORT", "8000"))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        run_bot_with_retry(),
    )


if __name__ == "__main__":
    asyncio.run(start_services())