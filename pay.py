import re
import urllib.parse
from config import BANK_ID, ACCOUNT_NO, ACCOUNT_NAME, SEPAY_QR_TEMPLATE

SEPAY_QR_BASE = "https://qr.sepay.vn/img"


def qr_url(amount: int, ref: str) -> str:
    acc  = ACCOUNT_NO.replace(" ", "")
    desc = urllib.parse.quote(ref, safe="")
    return (
        f"{SEPAY_QR_BASE}"
        f"?acc={acc}"
        f"&bank={BANK_ID}"
        f"&amount={amount}"
        f"&des={desc}"
        f"&template={SEPAY_QR_TEMPLATE}"
    )


def fmt(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


def parse_ref(text: str) -> str | None:
    m = re.search(
        r"(KEOCALL[A-Z0-9]{6,16}|TOOL[A-Z0-9]{6,16}|NAPTIEN[A-Z0-9]{6,16}"
        r"|ORDER[A-Z0-9]{6,16}|SVC[A-Z0-9]{8,16})",
        text.upper()
    )
    return m.group(1) if m else None


def calc_unit_price(pkgs: list[dict]) -> float:
    if not pkgs:
        return 20.0
    return pkgs[0]["price"] / pkgs[0]["qty"]