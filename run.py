import asyncio
import logging
import signal
from aiohttp import web

from config import WEBHOOK_PORT, SEED_ADMIN_IDS
import db
import bot1
import bot2
import hook

logging.basicConfig(
    format="%(asctime)s │ %(name)-20s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("run")


async def main():
    db.init()
    log.info("Database initialised")

    for aid in SEED_ADMIN_IDS:
        db.add_admin(aid, "seed")
        log.info(f"Seeded admin: {aid}")

    app1 = bot1.build_app()
    app2 = bot2.build_app()
    log.info("Bots built")

    hook.set_credit_callback(bot1.credit_from_webhook)
    hook.set_notify_admin_callback(bot1.notify_admin_tx)

    poll_task   = asyncio.create_task(hook.poll_sepay_loop(),  name="sepay-poll")
    expire_task = asyncio.create_task(
        hook.expire_qr_loop(app1.bot, bot1.kb_main), name="qr-expire"
    )
    log.info("SePay polling task started")

    server = hook.build_server()
    runner = web.AppRunner(server)
    await runner.setup()
    site   = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    log.info(f"Webhook server → 0.0.0.0:{WEBHOOK_PORT}")
    log.info(f"  SePay  → POST /webhook/sepay")
    log.info(f"  Health → GET  /health")

    await app1.initialize()
    await app2.initialize()
    await app1.start()
    await app2.start()
    await app1.updater.start_polling(drop_pending_updates=True)
    await app2.updater.start_polling(drop_pending_updates=True)
    log.info("Both bots polling ✓")

    stop_event = asyncio.Event()

    def _stop(sig, _frame):
        log.info(f"Received {sig.name} — shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    await stop_event.wait()
    log.info("Stopping...")

    poll_task.cancel()
    expire_task.cancel()
    for t in (poll_task, expire_task):
        try:
            await t
        except asyncio.CancelledError:
            pass

    await app1.updater.stop()
    await app2.updater.stop()
    await app1.stop()
    await app2.stop()
    await app1.shutdown()
    await app2.shutdown()
    await runner.cleanup()
    log.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())