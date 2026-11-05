TOOL_PRICE = 150_000
TOOL_NAME  = "TOOL LCMD5"

KEO_CALL_PRICE = 100_000
KEO_CALL_NAME  = "Kéo Call Đọc Bài"

# ─── SOURCE CODE ───────────────────────────────────────────────
SOURCE_BANKVIP_PRICE = 100_000
SOURCE_BANKVIP_NAME  = "Source BankVIP"

SOURCE_KIEMLUA_PRICE = 200_000
SOURCE_KIEMLUA_NAME  = "Source KiemLua"

SOURCE_ATDRAK_PRICE  = 249_000
SOURCE_ATDRAK_NAME   = "Source AtDrak (MiniApp)"

# map key → (price, name, filename)   ← filenames khớp với file thực tế trên server
SOURCE_CATALOG = {
    "bankvip": (SOURCE_BANKVIP_PRICE, SOURCE_BANKVIP_NAME,  "bankvip-source.zip"),
    "kiemlua": (SOURCE_KIEMLUA_PRICE, SOURCE_KIEMLUA_NAME,  "kiemlua-source.zip"),
    "atdrak":  (SOURCE_ATDRAK_PRICE,  SOURCE_ATDRAK_NAME,   "miniapp1.zip"),
}
