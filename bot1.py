import uuid
import json
import logging
import os
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode
from telegram import Bot as _TgBot

from config import (
    BOT1_TOKEN, BOT2_TOKEN, ADMIN_CHAT_ID,
    ACCOUNT_NAME as BANK_NAME, ACCOUNT_NO as BANK_ACC,
)
import db, pay, svc, hook

log = logging.getLogger(__name__)

_admin_bot = _TgBot(token=BOT2_TOKEN)

TOOL_PRICE     = svc.TOOL_PRICE
TOOL_NAME      = svc.TOOL_NAME
KEO_CALL_PRICE = svc.KEO_CALL_PRICE
KEO_CALL_NAME  = svc.KEO_CALL_NAME

SOURCE_BANKVIP_PRICE = svc.SOURCE_BANKVIP_PRICE
SOURCE_BANKVIP_NAME  = svc.SOURCE_BANKVIP_NAME
SOURCE_KIEMLUA_PRICE = svc.SOURCE_KIEMLUA_PRICE
SOURCE_KIEMLUA_NAME  = svc.SOURCE_KIEMLUA_NAME
SOURCE_ATDRAK_PRICE  = svc.SOURCE_ATDRAK_PRICE
SOURCE_ATDRAK_NAME   = svc.SOURCE_ATDRAK_NAME
SOURCE_CATALOG       = svc.SOURCE_CATALOG


# ─── Source helpers ───────────────────────────────────────────────

def _get_source_path(key: str) -> str:
    import os
    _, _, filename = SOURCE_CATALOG[key]
    return os.path.join(os.path.dirname(__file__), filename)


async def _deliver_source(bot, uid: int, key: str, amount: int):
    _, src_name, filename = SOURCE_CATALOG[key]
    msg = (
        f"🎉 <b>THANH TOÁN THÀNH CÔNG!</b>\n"
        f"─────────────────────────────\n"
        f"📂 Source: <b>{src_name}</b>\n"
        f"💵 Đã thanh toán: <b>{pay.fmt(amount)}</b>\n"
        f"─────────────────────────────\n"
        f"📥 File source được gửi ngay bên dưới."
    )
    await bot.send_message(uid, msg, parse_mode=ParseMode.HTML, reply_markup=kb_main())
    try:
        with open(_get_source_path(key), "rb") as f:
            await bot.send_document(
                uid,
                document=f,
                filename=filename,
                caption=(
                    f"📂 <b>{src_name}</b>\n"
                    f"💵 Đã thanh toán: <b>{pay.fmt(amount)}</b>"
                ),
                parse_mode=ParseMode.HTML,
            )
    except FileNotFoundError:
        log.error(f"Source file không tồn tại — {filename}")
        await bot.send_message(uid, "⚠️ File source đang được bảo trì, admin sẽ gửi lại sớm.")
    except Exception as e:
        log.error(f"send_document source {key} → {uid}: {e}")


async def _notify_admin_source_sold(uid: int, amount: int, ref: str,
                                    via: str, src_name: str):
    user  = db.get_user(uid)
    uname = (user.get("username") or "─") if user else "─"
    fname = (user.get("full_name") or "─") if user else "─"
    await _send_admin(
        f"📂 <b>SOURCE ĐÃ BÁN — {via}</b>\n"
        f"─────────────────────────────\n"
        f"📦 Source: <b>{src_name}</b>\n"
        f"👤 @{uname} (<code>{uid}</code>) — {fname}\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


async def show_qr_source(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         key: str, amount: int):
    src_price, src_name, _ = SOURCE_CATALOG[key]
    user   = await ensure_user(update)
    ref    = f"SRC{key.upper()}{uuid.uuid4().hex[:8].upper()}"
    qr_img = pay.qr_url(amount, ref)

    caption = (
        f"💳 <b>THANH TOÁN MUA SOURCE</b>\n"
        f"─────────────────────────────\n"
        f"📂 Source: <b>{src_name}</b>\n"
        f"💰 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        f"🏦 Ngân hàng: <b>TPBank</b>\n"
        f"👤 Chủ TK: <b>{BANK_NAME}</b>\n"
        f"🔢 Số TK: <code>{BANK_ACC}</code>\n"
        f"─────────────────────────────\n"
        f"📌 <b>NỘI DUNG CHUYỂN KHOẢN (BẮT BUỘC):</b>\n"
        f"<code>{ref}</code>\n"
        f"─────────────────────────────\n"
        f"⏰ QR hết hạn sau <b>15 phút</b>\n"
        f"✅ Hệ thống tự gửi file source ngay khi nhận tiền"
    )
    kb = kb_payment(ref)

    await _cleanup_old_qr(ctx, user["user_id"], f"SRC{key.upper()}")
    pmt_id = db.new_payment(user["user_id"], amount, ref,
                            {"source_key": key}, 0)

    chat_id  = update.effective_chat.id
    sent_msg = None
    try:
        sent_msg = await ctx.bot.send_photo(
            chat_id=chat_id, photo=qr_img, caption=caption,
            reply_markup=kb, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.warning(f"QR source photo failed ({e}) — text fallback")
        fallback = caption + f"\n\n🔗 <a href='{qr_img}'>Nhấn đây để xem mã QR</a>"
        try:
            sent_msg = await ctx.bot.send_message(
                chat_id, fallback, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        except Exception as e2:
            log.error(f"QR source text fallback failed: {e2}")

    if sent_msg:
        db.set_payment_msg_id(pmt_id, sent_msg.message_id)

    asyncio.create_task(hook.watch_payment_ref(ref, user["user_id"]))

    await _send_admin(
        f"🔔 <b>QR SOURCE ĐANG CHỜ THANH TOÁN</b>\n"
        f"📂 Source: <b>{src_name}</b>\n"
        f"👤 UID: <code>{user['user_id']}</code>  @{user.get('username','─')}\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


def _get_tool_path() -> str:
    base = os.path.dirname(__file__)
    for name in ("lc79md5.html", "lc79.html"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(base, "lc79md5.html")

LC79_PATH = _get_tool_path()


async def _send_admin(text: str):
    try:
        await _admin_bot.send_message(ADMIN_CHAT_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning(f"_send_admin → {ADMIN_CHAT_ID}: {e}")


async def notify_admin_tx(amount: int, desc: str, user: dict | None,
                          ref: str | None, matched: bool):
    if matched and user:
        uname   = user.get("username") or "─"
        fname   = user.get("full_name") or "─"
        uid     = user["user_id"]
        balance = user.get("balance", 0)
        user_txt = (
            f"👤 <b>User:</b>\n"
            f"🆔 <code>{uid}</code>\n"
            f"📛 @{uname} — {fname}\n"
            f"💰 Số dư: <b>{pay.fmt(balance)}</b>"
        )
        status_txt = f"✅ Khớp: <code>{ref}</code>"
        icon = "✅"
    else:
        user_txt = "👤 <b>User:</b> Không xác định"
        status_txt = "─"
        icon = "⚠️"

    text = (
        f"💰 <b>TIỀN VÀO</b> {icon}\n"
        f"─────────────────────────────\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📝 <code>{desc[:120]}</code>\n"
        f"📌 {status_txt}\n"
        f"─────────────────────────────\n"
        f"{user_txt}"
    )
    await _send_admin(text)


async def _notify_admin_tool_sold(uid: int, amount: int, ref: str, via: str):
    user  = db.get_user(uid)
    uname = (user.get("username") or "─") if user else "─"
    fname = (user.get("full_name") or "─") if user else "─"
    await _send_admin(
        f"🛠️ <b>TOOL ĐÃ BÁN — {via}</b>\n"
        f"─────────────────────────────\n"
        f"👤 @{uname} (<code>{uid}</code>) — {fname}\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


async def _notify_admin_keocall_sold(uid: int, amount: int, ref: str,
                                     via: str, oid: str):
    user  = db.get_user(uid)
    uname = (user.get("username") or "─") if user else "─"
    fname = (user.get("full_name") or "─") if user else "─"
    await _send_admin(
        f"🎯 <b>ĐƠN KÉO CALL — {via}</b>\n"
        f"─────────────────────────────\n"
        f"👤 @{uname} (<code>{uid}</code>) — {fname}\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📦 Order: <code>{oid}</code>\n"
        f"📌 Ref: <code>{ref}</code>\n"
        f"⚡ <i>Admin liên hệ user để xử lý đơn.</i>"
    )


def txt_welcome(u: dict) -> str:
    name = u.get("full_name") or u.get("username") or "Khách"
    return (
        "╔═══════════════════════════╗\n"
        "║                🛠️ AI DEV SHOP  ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👋 Chào <b>{name}</b>!\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"💰 Số dư: <b>{pay.fmt(u['balance'])}</b>\n"
        f"📦 Tổng đơn: <b>{u['total_orders']}</b>\n\n"
        "⬇️ Chọn thao tác bên dưới:"
    )


def txt_profile(u: dict) -> str:
    return (
        "👤 <b>THÔNG TIN TÀI KHOẢN</b>\n"
        "─────────────────────────────\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"👤 Tên: <b>{u.get('full_name','─')}</b>\n"
        f"📛 Username: @{u.get('username','─')}\n"
        f"💰 Số dư: <b>{pay.fmt(u['balance'])}</b>\n"
        f"📦 Tổng đơn: <b>{u['total_orders']}</b>\n"
        f"💸 Đã chi: <b>{pay.fmt(u['total_spent'])}</b>\n"
        "─────────────────────────────"
    )


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🛠️  Mua Tool — {pay.fmt(TOOL_PRICE)}",
            callback_data="buy_tool"
        )],
        [InlineKeyboardButton(
            f"🎯  Kéo Call Đọc Bài — {pay.fmt(KEO_CALL_PRICE)}",
            callback_data="buy_keocall"
        )],
        [InlineKeyboardButton("📂  Mua Source Code", callback_data="menu:source")],
        [
            InlineKeyboardButton("💳 Nạp Tiền",  callback_data="menu:topup"),
            InlineKeyboardButton("👤 Tài Khoản", callback_data="menu:profile"),
        ],
    ])


def kb_confirm_tool(ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác Nhận & Mua", callback_data=f"confirm_tool:{ref}"),
            InlineKeyboardButton("❌ Huỷ",             callback_data="back:main"),
        ]
    ])


def kb_confirm_keocall(ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác Nhận & Mua", callback_data=f"confirm_keocall:{ref}"),
            InlineKeyboardButton("❌ Huỷ",             callback_data="back:main"),
        ]
    ])


def kb_payment(ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Kiểm Tra Thanh Toán", callback_data=f"chkpay:{ref}")],
        [InlineKeyboardButton("❌ Huỷ Thanh Toán",      callback_data=f"cancelpay:{ref}")],
    ])


def kb_topup() -> InlineKeyboardMarkup:
    amounts = [50_000, 100_000, 200_000, 500_000, 1_000_000]
    rows, row = [], []
    for a in amounts:
        row.append(InlineKeyboardButton(pay.fmt(a), callback_data=f"tamt:{a}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows += [
        [InlineKeyboardButton("✏️ Nhập Số Tiền Khác", callback_data="tamt:custom")],
        [InlineKeyboardButton("◀️ Quay Lại",          callback_data="back:main")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_back(target: str = "main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Quay Lại", callback_data=f"back:{target}")]
    ])


def kb_confirm_source(key: str, ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác Nhận & Mua", callback_data=f"confirm_source:{key}:{ref}"),
            InlineKeyboardButton("❌ Huỷ",             callback_data="back:main"),
        ]
    ])


def kb_source() -> InlineKeyboardMarkup:
    """Menu chọn source — hiện ra khi user bấm 'Mua Source Code'."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"💎  Source BankVIP — {pay.fmt(SOURCE_BANKVIP_PRICE)}",
            callback_data="buy_source:bankvip"
        )],
        [InlineKeyboardButton(
            f"💰  Source KiemLua — {pay.fmt(SOURCE_KIEMLUA_PRICE)}",
            callback_data="buy_source:kiemlua"
        )],
        [InlineKeyboardButton(
            f"📱  Source AtDrak (MiniApp) — {pay.fmt(SOURCE_ATDRAK_PRICE)}",
            callback_data="buy_source:atdrak"
        )],
        [InlineKeyboardButton("◀️ Quay Lại", callback_data="back:main")],
    ])


async def ensure_user(update: Update) -> dict:
    u    = update.effective_user
    user = db.get_user(u.id)
    if not user:
        user = db.upsert_user(u.id, u.username or "", u.full_name or "")
    return user


async def reply(update: Update, text: str,
                kb: InlineKeyboardMarkup | None = None,
                parse_mode: str = ParseMode.HTML):
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=kb, parse_mode=parse_mode
            )
        else:
            await update.message.reply_text(text, reply_markup=kb, parse_mode=parse_mode)
    except Exception:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(text, reply_markup=kb, parse_mode=parse_mode)


async def show_main(update: Update, _: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    await reply(update, txt_welcome(user), kb_main())


async def _deliver_tool(bot, uid: int, amount: int):
    msg = (
        "🎉 <b>THANH TOÁN THÀNH CÔNG!</b>\n"
        "─────────────────────────────\n"
        f"🛠️ Sản phẩm: <b>{TOOL_NAME}</b>\n"
        f"💵 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        "─────────────────────────────\n"
        "📥 File tool được gửi kèm bên dưới.\n"
        "⚠️ <b>Không chia sẻ cho người khác!</b>"
    )
    await bot.send_message(uid, msg, parse_mode=ParseMode.HTML, reply_markup=kb_main())

    try:
        with open(LC79_PATH, "rb") as f:
            await bot.send_document(
                uid,
                document=f,
                filename="lc79md5.html",
                caption=(
                    f"🛠️ <b>{TOOL_NAME}</b>\n"
                    f"💵 Đã thanh toán: <b>{pay.fmt(amount)}</b>\n\n"
                    "📌 Mở file bằng trình duyệt để dùng tool."
                ),
                parse_mode=ParseMode.HTML,
            )
    except FileNotFoundError:
        log.error("Tool file không tồn tại — kiểm tra lc79md5.html")
        await bot.send_message(uid, "⚠️ File tool đang được bảo trì, admin sẽ gửi lại sớm.")
    except Exception as e:
        log.error(f"send_document tool → {uid}: {e}")


async def _deliver_keocall(bot, uid: int, amount: int, ref: str) -> str:
    oid = db.new_order(uid, "KEOCALL", KEO_CALL_NAME, amount, ref)
    db.set_order_status(oid, "processing", "Chờ admin xử lý")

    msg = (
        "🎉 <b>ĐẶT MUA THÀNH CÔNG!</b>\n"
        "─────────────────────────────\n"
        f"🎯 Dịch vụ: <b>{KEO_CALL_NAME}</b>\n"
        f"💵 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        f"📦 Mã đơn: <code>{oid}</code>\n"
        "─────────────────────────────\n"
        "⏳ Đơn đang được xử lý.\n"
        "📞 Admin sẽ liên hệ bạn trong thời gian sớm nhất!"
    )
    try:
        await bot.send_message(uid, msg, parse_mode=ParseMode.HTML, reply_markup=kb_main())
    except Exception as e:
        log.error(f"deliver_keocall notify uid={uid}: {e}")
    return oid


async def _cleanup_old_qr(ctx: ContextTypes.DEFAULT_TYPE, uid: int,
                          prefix: str, skip_pmt_id: str = ""):
    for old in db.get_user_pending_payments(uid, prefix):
        if skip_pmt_id and old["payment_id"] == skip_pmt_id:
            continue
        if not db.expire_payment(old["payment_id"]):
            continue
        if old.get("message_id"):
            try:
                await ctx.bot.delete_message(chat_id=uid, message_id=old["message_id"])
            except Exception:
                pass


async def show_qr_tool(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    user   = await ensure_user(update)
    ref    = f"TOOL{uuid.uuid4().hex[:8].upper()}"
    qr_img = pay.qr_url(amount, ref)

    caption = (
        "💳 <b>THANH TOÁN MUA TOOL</b>\n"
        "─────────────────────────────\n"
        f"🛠️ Sản phẩm: <b>{TOOL_NAME}</b>\n"
        f"💰 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        f"🏦 Ngân hàng: <b>TPBank</b>\n"
        f"👤 Chủ TK: <b>{BANK_NAME}</b>\n"
        f"🔢 Số TK: <code>{BANK_ACC}</code>\n"
        "─────────────────────────────\n"
        "📌 <b>NỘI DUNG CHUYỂN KHOẢN (BẮT BUỘC):</b>\n"
        f"<code>{ref}</code>\n"
        "─────────────────────────────\n"
        "⏰ QR hết hạn sau <b>15 phút</b>\n"
        "✅ Hệ thống tự gửi file tool ngay khi nhận tiền"
    )
    kb = kb_payment(ref)

    await _cleanup_old_qr(ctx, user["user_id"], "TOOL")
    pmt_id = db.new_payment(user["user_id"], amount, ref, {"tool": True}, 0)

    chat_id  = update.effective_chat.id
    sent_msg = None
    try:
        sent_msg = await ctx.bot.send_photo(
            chat_id=chat_id, photo=qr_img, caption=caption,
            reply_markup=kb, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.warning(f"QR photo failed ({e}) — text fallback")
        fallback = caption + f"\n\n🔗 <a href='{qr_img}'>Nhấn đây để xem mã QR</a>"
        try:
            sent_msg = await ctx.bot.send_message(
                chat_id, fallback, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        except Exception as e2:
            log.error(f"QR text fallback failed: {e2}")

    if sent_msg:
        db.set_payment_msg_id(pmt_id, sent_msg.message_id)

    asyncio.create_task(hook.watch_payment_ref(ref, user["user_id"]))

    await _send_admin(
        f"🔔 <b>QR TOOL ĐANG CHỜ THANH TOÁN</b>\n"
        f"👤 UID: <code>{user['user_id']}</code>  @{user.get('username','─')}\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


async def show_qr_keocall(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    user   = await ensure_user(update)
    ref    = f"KEOCALL{uuid.uuid4().hex[:8].upper()}"
    qr_img = pay.qr_url(amount, ref)

    caption = (
        "💳 <b>THANH TOÁN MUA KÉO CALL</b>\n"
        "─────────────────────────────\n"
        f"🎯 Dịch vụ: <b>{KEO_CALL_NAME}</b>\n"
        f"💰 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        f"🏦 Ngân hàng: <b>TPBank</b>\n"
        f"👤 Chủ TK: <b>{BANK_NAME}</b>\n"
        f"🔢 Số TK: <code>{BANK_ACC}</code>\n"
        "─────────────────────────────\n"
        "📌 <b>NỘI DUNG CHUYỂN KHOẢN (BẮT BUỘC):</b>\n"
        f"<code>{ref}</code>\n"
        "─────────────────────────────\n"
        "⏰ QR hết hạn sau <b>15 phút</b>\n"
        "✅ Admin sẽ liên hệ xử lý ngay khi nhận tiền"
    )
    kb = kb_payment(ref)

    await _cleanup_old_qr(ctx, user["user_id"], "KEOCALL")
    pmt_id = db.new_payment(user["user_id"], amount, ref, {"keocall": True}, 0)

    chat_id  = update.effective_chat.id
    sent_msg = None
    try:
        sent_msg = await ctx.bot.send_photo(
            chat_id=chat_id, photo=qr_img, caption=caption,
            reply_markup=kb, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.warning(f"QR keocall photo failed ({e}) — text fallback")
        fallback = caption + f"\n\n🔗 <a href='{qr_img}'>Nhấn đây để xem mã QR</a>"
        try:
            sent_msg = await ctx.bot.send_message(
                chat_id, fallback, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        except Exception as e2:
            log.error(f"QR keocall text fallback failed: {e2}")

    if sent_msg:
        db.set_payment_msg_id(pmt_id, sent_msg.message_id)

    asyncio.create_task(hook.watch_payment_ref(ref, user["user_id"]))

    await _send_admin(
        f"🔔 <b>QR KÉO CALL ĐANG CHỜ THANH TOÁN</b>\n"
        f"👤 UID: <code>{user['user_id']}</code>  @{user.get('username','─')}\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


async def show_qr_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    user   = await ensure_user(update)
    ref    = f"NAPTIEN{uuid.uuid4().hex[:8].upper()}"
    qr_img = pay.qr_url(amount, ref)

    caption = (
        "💳 <b>NẠP TIỀN VÀO SỐ DƯ</b>\n"
        "─────────────────────────────\n"
        f"💰 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        f"🏦 Ngân hàng: <b>TPBank</b>\n"
        f"👤 Chủ TK: <b>{BANK_NAME}</b>\n"
        f"🔢 Số TK: <code>{BANK_ACC}</code>\n"
        "─────────────────────────────\n"
        "📌 <b>NỘI DUNG CHUYỂN KHOẢN (BẮT BUỘC):</b>\n"
        f"<code>{ref}</code>\n"
        "─────────────────────────────\n"
        "⏰ Hết hạn sau <b>15 phút</b>\n"
        "✅ Số dư tự cộng sau khi nhận tiền"
    )
    kb = kb_payment(ref)

    await _cleanup_old_qr(ctx, user["user_id"], "NAPTIEN")
    pmt_id = db.new_payment(user["user_id"], amount, ref, {}, 0)

    chat_id  = update.effective_chat.id
    sent_msg = None
    try:
        sent_msg = await ctx.bot.send_photo(
            chat_id=chat_id, photo=qr_img, caption=caption,
            reply_markup=kb, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.warning(f"Topup QR photo failed ({e})")
        fallback = caption + f"\n\n🔗 <a href='{qr_img}'>Xem mã QR</a>"
        try:
            sent_msg = await ctx.bot.send_message(
                chat_id, fallback, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        except Exception as e2:
            log.error(f"Topup QR fallback failed: {e2}")

    if sent_msg:
        db.set_payment_msg_id(pmt_id, sent_msg.message_id)

    asyncio.create_task(hook.watch_payment_ref(ref, user["user_id"]))


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    if user.get("is_banned"):
        await update.message.reply_text("⛔ Tài khoản của bạn đã bị khoá.")
        return
    await show_main(update, ctx)


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    await update.message.reply_text(
        f"💰 Số dư: <b>{pay.fmt(user['balance'])}</b>",
        parse_mode=ParseMode.HTML
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = await ensure_user(update)
    if user.get("is_banned"):
        await q.answer("⛔ Tài khoản bị khoá!", show_alert=True)
        return
    await q.answer()
    data = q.data

    if data.startswith("back:"):
        if data[5:] == "main":
            await show_main(update, ctx)
        return

    if data == "buy_tool":
        bal     = user["balance"]
        can_pay = bal >= TOOL_PRICE
        status  = (
            "✅ <b>Số dư đủ — sẽ trừ trực tiếp</b>"
            if can_pay
            else f"⚠️ <b>Thiếu {pay.fmt(TOOL_PRICE - bal)} → thanh toán QR</b>"
        )
        ref = f"TOOL{uuid.uuid4().hex[:8].upper()}"
        ctx.user_data["tool_pending"] = {"ref": ref}
        txt = (
            "🛠️ <b>XÁC NHẬN MUA TOOL</b>\n"
            "─────────────────────────────\n"
            f"📦 Sản phẩm: <b>{TOOL_NAME}</b>\n"
            f"💵 Giá: <b>{pay.fmt(TOOL_PRICE)}</b>\n"
            "─────────────────────────────\n"
            f"💼 Số dư của bạn: <b>{pay.fmt(bal)}</b>\n"
            f"{status}\n\n"
            "📥 File tool được gửi ngay sau khi thanh toán xong."
        )
        await reply(update, txt, kb_confirm_tool(ref))
        return

    if data == "buy_keocall":
        bal     = user["balance"]
        can_pay = bal >= KEO_CALL_PRICE
        if can_pay:
            status = "✅ <b>Vốn đủ — sẽ trừ trực tiếp</b>"
        else:
            status = f"⚠️ <b>Thiếu {pay.fmt(KEO_CALL_PRICE - bal)} → thanh toán QR</b>"
        ref = f"KEOCALL{uuid.uuid4().hex[:8].upper()}"
        ctx.user_data["keocall_pending"] = {"ref": ref}
        txt = (
            "🎯 <b>XÁC NHẬN MUA KÉO CALL</b>\n"
            "─────────────────────────────\n"
            f"📦 Dịch vụ: <b>{KEO_CALL_NAME}</b>\n"
            f"💵 Giá: <b>{pay.fmt(KEO_CALL_PRICE)}</b>\n"
            "─────────────────────────────\n"
            f"💼 Vốn hiện tại: <b>{pay.fmt(bal)}</b>\n"
            f"📌 <b>Yêu cầu: Vốn ≥ {pay.fmt(KEO_CALL_PRICE)}</b>\n"
            f"{status}\n\n"
            "⏳ Sau khi thanh toán, admin sẽ liên hệ xử lý đơn."
        )
        await reply(update, txt, kb_confirm_keocall(ref))
        return

    if data.startswith("buy_source:"):
        key = data[11:]
        if key not in SOURCE_CATALOG:
            await q.answer("Source không tồn tại!", show_alert=True)
            return
        src_price, src_name, _ = SOURCE_CATALOG[key]
        bal     = user["balance"]
        can_pay = bal >= src_price
        status  = (
            "✅ <b>Số dư đủ — sẽ trừ trực tiếp</b>"
            if can_pay
            else f"⚠️ <b>Thiếu {pay.fmt(src_price - bal)} → thanh toán QR</b>"
        )
        ref = f"SRC{key.upper()}{uuid.uuid4().hex[:8].upper()}"
        ctx.user_data["source_pending"] = {"key": key, "ref": ref}
        txt = (
            f"📂 <b>XÁC NHẬN MUA SOURCE</b>\n"
            f"─────────────────────────────\n"
            f"📦 Source: <b>{src_name}</b>\n"
            f"💵 Giá: <b>{pay.fmt(src_price)}</b>\n"
            f"─────────────────────────────\n"
            f"💼 Số dư của bạn: <b>{pay.fmt(bal)}</b>\n"
            f"{status}\n\n"
            f"📥 File source được gửi ngay sau khi thanh toán xong."
        )
        await reply(update, txt, kb_confirm_source(key, ref))
        return

    if data.startswith("confirm_source:"):
        parts = data.split(":", 2)          # confirm_source:key:ref
        if len(parts) != 3:
            await q.answer("Dữ liệu lỗi!", show_alert=True)
            return
        _, key, ref = parts
        if key not in SOURCE_CATALOG:
            await q.answer("Source không tồn tại!", show_alert=True)
            return
        pend = ctx.user_data.get("source_pending")
        if not pend or pend.get("ref") != ref or pend.get("key") != key:
            await q.answer("Phiên hết hạn, chọn lại!", show_alert=True)
            return

        src_price, src_name, _ = SOURCE_CATALOG[key]
        ok = db.deduct(user["user_id"], src_price,
                       ref_id=ref, desc=f"Mua {src_name}")
        if not ok:
            try:
                await q.message.delete()
            except Exception:
                pass
            await show_qr_source(update, ctx, key, src_price)
            return

        oid = db.new_order(user["user_id"], "SOURCE", src_name, src_price, ref)
        db.set_order_status(oid, "completed", "Mua bằng số dư")

        try:
            await q.message.delete()
        except Exception:
            pass

        await _deliver_source(ctx.bot, user["user_id"], key, src_price)
        await _notify_admin_source_sold(user["user_id"], src_price, ref, "Số dư", src_name)
        ctx.user_data.pop("source_pending", None)
        return

    if data == "menu:source":
        txt = (
            "📂 <b>MUA SOURCE CODE</b>\n"
            "─────────────────────────────\n"
            "💎 <b>Source BankVIP</b> — " + pay.fmt(SOURCE_BANKVIP_PRICE) + "\n"
            "   └ Source trang web giả ngân hàng (BankVIP)\n\n"
            "💰 <b>Source KiemLua</b> — " + pay.fmt(SOURCE_KIEMLUA_PRICE) + "\n"
            "   └ Source tool kiếm lua tự động\n\n"
            "📱 <b>Source AtDrak (MiniApp)</b> — " + pay.fmt(SOURCE_ATDRAK_PRICE) + "\n"
            "   └ Source mini app lừa đảo\n"
            "─────────────────────────────\n"
            "⬇️ Chọn source muốn mua:"
        )
        await reply(update, txt, kb_source())
        return

    if data.startswith("confirm_tool:"):
        ref  = data[13:]
        pend = ctx.user_data.get("tool_pending")
        if not pend or pend.get("ref") != ref:
            await q.answer("Phiên hết hạn, chọn lại!", show_alert=True)
            return

        ok = db.deduct(user["user_id"], TOOL_PRICE,
                       ref_id=ref, desc=f"Mua {TOOL_NAME}")
        if not ok:
            try:
                await q.message.delete()
            except Exception:
                pass
            await show_qr_tool(update, ctx, TOOL_PRICE)
            return

        oid = db.new_order(user["user_id"], "TOOL", TOOL_NAME, TOOL_PRICE, ref)
        db.set_order_status(oid, "completed", "Mua bằng số dư")

        try:
            await q.message.delete()
        except Exception:
            pass

        await _deliver_tool(ctx.bot, user["user_id"], TOOL_PRICE)
        await _notify_admin_tool_sold(user["user_id"], TOOL_PRICE, ref, "Số dư")
        ctx.user_data.pop("tool_pending", None)
        return

    if data.startswith("confirm_keocall:"):
        ref  = data[16:]
        pend = ctx.user_data.get("keocall_pending")
        if not pend or pend.get("ref") != ref:
            await q.answer("Phiên hết hạn, chọn lại!", show_alert=True)
            return

        ok = db.deduct(user["user_id"], KEO_CALL_PRICE,
                       ref_id=ref, desc=f"Mua {KEO_CALL_NAME}")
        if not ok:
            try:
                await q.message.delete()
            except Exception:
                pass
            await show_qr_keocall(update, ctx, KEO_CALL_PRICE)
            return

        try:
            await q.message.delete()
        except Exception:
            pass

        oid = await _deliver_keocall(ctx.bot, user["user_id"], KEO_CALL_PRICE, ref)
        await _notify_admin_keocall_sold(user["user_id"], KEO_CALL_PRICE, ref, "Số dư", oid)
        ctx.user_data.pop("keocall_pending", None)
        return

    if data == "menu:topup":
        await reply(update,
            "💳 <b>NẠP TIỀN VÀO SỐ DƯ</b>\n\nChọn mệnh giá muốn nạp:",
            kb_topup()
        )
        return

    if data == "menu:profile":
        await reply(update, txt_profile(user), kb_back("main"))
        return

    if data.startswith("chkpay:"):
        ref = data[7:]
        pmt = db.get_payment_by_ref(ref)
        if not pmt:
            await q.answer("Đơn không tồn tại hoặc đã xử lý xong!", show_alert=True)
            return
        await q.answer("⏳ Đang kiểm tra SePay...", show_alert=False)
        found = await hook.check_payment_now(ref)
        await q.answer(
            "✅ Đã tìm thấy giao dịch!" if found
            else "⏳ Chưa thấy. Chờ 1–2 phút sau khi CK rồi thử lại.",
            show_alert=True
        )
        return

    if data.startswith("cancelpay:"):
        ref = data[10:]
        pmt = db.get_payment_by_ref(ref)
        if pmt:
            db.expire_payment(pmt["payment_id"])
        await reply(update,
            "❌ <b>Đã huỷ thanh toán.</b>\n\nBạn có thể mua lại bất cứ lúc nào.",
            kb_main()
        )
        return

    if data.startswith("tamt:"):
        val = data[5:]
        if val == "custom":
            ctx.user_data["await"]          = "topup_custom"
            ctx.user_data["prompt_msg_id"]  = q.message.message_id
            ctx.user_data["prompt_chat_id"] = q.message.chat_id
            await reply(update,
                "✏️ <b>NHẬP SỐ TIỀN NẠP</b>\n\nTối thiểu: <b>10.000đ</b>\n\nNhập số tiền (VD: 150000 hoặc 150K):",
                InlineKeyboardMarkup([[InlineKeyboardButton("❌ Huỷ", callback_data="back:main")]]))
            return
        try:
            await q.message.delete()
        except Exception:
            pass
        await show_qr_topup(update, ctx, int(val))
        return


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = ctx.user_data.get("await")
    if not state:
        await show_main(update, ctx)
        return

    text = update.message.text.strip()

    def clean_num(s: str) -> str:
        return s.replace(".", "").replace(",", "").replace("k", "000").replace("K", "000")

    if state == "topup_custom":
        cleaned = clean_num(text)
        if not cleaned.isdigit():
            await update.message.reply_text("❌ Nhập số tiền hợp lệ!")
            return
        amount = int(cleaned)
        if amount < 10_000:
            await update.message.reply_text(
                "❌ Tối thiểu <b>10.000đ</b>!", parse_mode=ParseMode.HTML
            )
            return
        ctx.user_data["await"] = None
        try:
            await update.message.delete()
        except Exception:
            pass
        pmid = ctx.user_data.pop("prompt_msg_id", None)
        pcid = ctx.user_data.pop("prompt_chat_id", None)
        if pmid and pcid:
            try:
                await ctx.bot.delete_message(chat_id=pcid, message_id=pmid)
            except Exception:
                pass
        await show_qr_topup(update, ctx, amount)
        return

    await show_main(update, ctx)


_app: Application = None


async def credit_from_webhook(uid: int, amount: int, ref: str, pmt_id: str):
    pmt = db.get_payment_by_id(pmt_id)
    if not pmt:
        log.warning(f"credit_from_webhook: payment not found: {pmt_id}")
        return
    if not _app:
        log.error("credit_from_webhook: _app chưa khởi tạo")
        return

    if not db.complete_payment(pmt_id):
        return

    bot    = _app.bot
    msg_id = pmt.get("message_id", 0) or 0

    if msg_id:
        try:
            await bot.delete_message(chat_id=uid, message_id=msg_id)
        except Exception as e:
            log.warning(f"delete QR msg {msg_id}: {e}")

    if ref.startswith("NAPTIEN"):
        prefix = "NAPTIEN"
    elif ref.startswith("KEOCALL"):
        prefix = "KEOCALL"
    else:
        prefix = "TOOL"

    for old in db.get_user_pending_payments(uid, prefix):
        if old["payment_id"] == pmt_id:
            continue
        if db.expire_payment(old["payment_id"]):
            try:
                if old.get("message_id"):
                    await bot.delete_message(chat_id=uid, message_id=old["message_id"])
            except Exception:
                pass

    if ref.startswith("SRC"):
        # determine which source key
        matched_key = None
        for k in SOURCE_CATALOG:
            if ref.startswith(f"SRC{k.upper()}"):
                matched_key = k
                break
        if matched_key is None:
            log.warning(f"credit_from_webhook: unknown SRC ref {ref}")
            return

        src_price, src_name, _ = SOURCE_CATALOG[matched_key]
        if amount < pmt["amount"]:
            await _send_admin(
                f"⚠️ <b>THANH TOÁN THIẾU — KHÔNG GIAO SOURCE</b>\n"
                f"📂 Source: <b>{src_name}</b>\n"
                f"👤 UID: <code>{uid}</code>\n"
                f"📌 Ref: <code>{ref}</code>\n"
                f"💵 Cần: <b>{pay.fmt(pmt['amount'])}</b>\n"
                f"💵 Nhận: <b>{pay.fmt(amount)}</b>"
            )
            return

        oid = db.new_order(uid, "SOURCE", src_name, amount, ref)
        db.set_order_status(oid, "completed", "Auto — giao source")

        try:
            await _deliver_source(bot, uid, matched_key, amount)
        except Exception as e:
            log.error(f"deliver_source {matched_key} uid={uid}: {e}")

        await _notify_admin_source_sold(uid, amount, ref, "QR", src_name)
        return

    if ref.startswith("TOOL"):
        if amount < pmt["amount"]:
            await _send_admin(
                f"⚠️ <b>THANH TOÁN THIẾU — KHÔNG GIAO TOOL</b>\n"
                f"👤 UID: <code>{uid}</code>\n"
                f"📌 Ref: <code>{ref}</code>\n"
                f"💵 Cần: <b>{pay.fmt(pmt['amount'])}</b>\n"
                f"💵 Nhận: <b>{pay.fmt(amount)}</b>"
            )
            return

        oid = db.new_order(uid, "TOOL", TOOL_NAME, amount, ref)
        db.set_order_status(oid, "completed", "Auto — giao tool")

        try:
            await _deliver_tool(bot, uid, amount)
        except Exception as e:
            log.error(f"deliver_tool uid={uid}: {e}")

        await _notify_admin_tool_sold(uid, amount, ref, "QR")
        return

    if ref.startswith("KEOCALL"):
        if amount < pmt["amount"]:
            await _send_admin(
                f"⚠️ <b>THANH TOÁN THIẾU — KHÔNG XỬ LÝ KÉO CALL</b>\n"
                f"👤 UID: <code>{uid}</code>\n"
                f"📌 Ref: <code>{ref}</code>\n"
                f"💵 Cần: <b>{pay.fmt(pmt['amount'])}</b>\n"
                f"💵 Nhận: <b>{pay.fmt(amount)}</b>"
            )
            return

        oid = await _deliver_keocall(bot, uid, amount, ref)
        await _notify_admin_keocall_sold(uid, amount, ref, "QR", oid)
        return

    db.credit(uid, amount, ref_id=ref, desc="Nạp tiền QR")
    user = db.get_user(uid)
    if not user:
        return
    try:
        await bot.send_message(
            uid,
            f"✅ <b>NẠP TIỀN THÀNH CÔNG!</b>\n"
            f"─────────────────────────────\n"
            f"💰 Số tiền: <b>{pay.fmt(amount)}</b>\n"
            f"💼 Số dư mới: <b>{pay.fmt(user['balance'])}</b>\n"
            f"📌 Ref: <code>{ref}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main()
        )
    except Exception as e:
        log.error(f"notify topup uid={uid}: {e}")

    await _send_admin(
        f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n"
        f"👤 UID: <code>{uid}</code>  @{user.get('username','─')}\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"💼 Số dư mới: <b>{pay.fmt(user['balance'])}</b>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


def build_app() -> Application:
    global _app
    app = Application.builder().token(BOT1_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    _app = app
    return app