"""پرکردن مونگوی محلی توسعه از CSVهای export‌شده‌ی نیروگاه.

**فقط ابزار توسعه.** در استقرار هیچ‌جا صدا زده نمی‌شود؛ تنها کارش این است که
قدم‌های بعدی (استنتاج schema، گارد، ایجنت، گراف) روی داده‌ی واقعی و قابل
تکرار تست شوند، نه روی داده‌ی ساختگی که هر بار عوض می‌شود.

سه تصمیم که چرایی‌شان مهم است:

۱. **تبدیل نوع «ستونی» است، نه «سلولی».** نوع هر ستون یک‌بار از روی همه‌ی
   مقادیرش استنتاج و بعد یکنواخت اعمال می‌شود. اگر سلول‌به‌سلول تصمیم بگیریم،
   یک فیلد در بعضی داکیومنت‌ها `int` و در بعضی `float` می‌شود و استنتاج schema
   در قدم ۳ یک فیلد را با دو نوع گزارش می‌کند.

۲. **ستون کاملاً خالی = فیلد غایب.** این CSVها را pandas نوشته: برای عدد گمشده
   `NaN` و برای متن گمشده رشته‌ی خالی. اگر همین‌ها را وارد کنیم، فیلدهایی
   می‌سازیم که وجود دارند ولی هیچ اطلاعاتی ندارند و «درصد حضور» در قدم ۳ دروغ
   می‌گوید. (`fa_al_message` و ۱۷ ستون `mvpanel_data` دقیقاً همین‌اند — به
   `docs/v1-reference.md` بخش ۱٫۴ مربوط‌اند.)

۳. **کاربر فقط-خواندنی از روی همان URI پروفایل ساخته می‌شود**، نه از یک متغیر
   جداگانه. این‌طور تضمین می‌شود رشته‌ی اتصالی که خود محصول استفاده می‌کند
   واقعاً کار می‌کند — قاعده‌ی غیرقابل مذاکره‌ی شماره‌ی ۲ روی کاغذ نمی‌ماند.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError, ServerSelectionTimeoutError
from pymongo.uri_parser import parse_uri

# اسکریپت مستقیم اجرا می‌شود (`python scripts/seed_mongo.py`)، پس پوشه‌ی
# `scripts/` روی sys.path می‌نشیند نه ریشه‌ی پروژه. بدون این خط `core` پیدا نمی‌شود.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# کنسول ویندوز اگر codepage غیر-UTF8 داشته باشد (cp1252 در Git Bash، cp437 در
# cmd قدیمی) چاپ فارسی را با UnicodeEncodeError می‌شکند — یعنی گزارش موفقیت
# وسط کار crash می‌کند. همه‌ی پیام‌های این پروژه فارسی‌اند، پس گارد لازم است.
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

from core.profiles import ProfileError, load_profile  # noqa: E402
from core.settings import PROJECT_ROOT  # noqa: E402

# نام متغیر محیطی اتصال ادمین. جداست از URI مشتری، چون seed می‌نویسد و کاربر
# مشتری اجازه‌ی نوشتن ندارد — و نباید هم داشته باشد.
ADMIN_URI_ENV = "MONGO_ADMIN_URI"

_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
# نشانه‌های «مقدار وجود ندارد» در خروجی pandas و mongoexport.
_MISSING = frozenset({"", "NaN", "nan", "null", "None"})


def _column_converter(column: str, values: list[str]):
    """نوع ستون را از روی مقادیر موجودش استنتاج و تابع تبدیل را برمی‌گرداند.

    ترتیب بررسی از خاص به عام است. `ObjectId` فقط برای `_id` امتحان می‌شود:
    یک رشته‌ی ۲۴ کاراکتری hex ممکن است در ستون دیگری هم پیدا شود و تبدیل
    ناخواسته‌اش داده را خراب می‌کند. `_id` نامِ رزروشده‌ی خود مونگوست، پس این
    استثنا hard-code کردن دانش پروژه نیست.
    """
    if column == "_id" and all(_OBJECT_ID.match(v) for v in values):
        return ObjectId
    if all(_ISO_UTC.match(v) for v in values):
        # `Z` را `fromisoformat` از پایتون ۳٫۱۱ می‌فهمد و datetime آگاه از
        # تایم‌زون می‌سازد؛ درایور مونگو خودش آن را به UTC ذخیره می‌کند.
        return datetime.fromisoformat
    if all(v in ("true", "false") for v in values):
        return lambda v: v == "true"
    for caster in (int, float):
        try:
            for value in values:
                caster(value)
            return caster
        except ValueError:
            continue
    return str


def _load_csv(path: Path) -> tuple[list[dict], list[str]]:
    """یک CSV را به فهرست داکیومنت مونگو تبدیل می‌کند.

    خروجی دوم فهرست ستون‌های کاملاً خالی است؛ اینها وارد نمی‌شوند ولی باید
    گزارش شوند، چون دانش دامنه‌ی قدم ۶ باید صادقانه بگوید کدام فیلدها در
    داده‌ی واقعی مقدار ندارند.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, restval=""))
    if not rows:
        return [], []

    documents: list[dict] = [{} for _ in rows]
    empty_columns: list[str] = []
    # ترتیب کلیدها از ترتیب ستون‌های CSV می‌آید تا داکیومنت خوانا بماند.
    for column in rows[0]:
        if column is None:  # ردیف ناهم‌اندازه؛ ستون بی‌نام
            continue
        raw = [str(row.get(column) or "") for row in rows]
        present = [value for value in raw if value not in _MISSING]
        if not present:
            empty_columns.append(column)
            continue
        convert = _column_converter(column, present)
        for document, value in zip(documents, raw):
            if value not in _MISSING:
                document[column] = convert(value)
    return documents, empty_columns


def _ensure_readonly_user(client: MongoClient, profile) -> str:
    """کاربر فقط-خواندنی را دقیقاً مطابق URI پروفایل می‌سازد یا به‌روز می‌کند.

    عمداً idempotent است: اجرای دوباره‌ی seed نباید با «کاربر تکراری» شکست
    بخورد، و اگر رمز در `.env` عوض شد باید همان‌جا اعمال شود.
    """
    parsed = parse_uri(profile.connection_uri())
    username, password = parsed["username"], parsed["password"]
    if not username or not password:
        raise ProfileError(
            f"رشته‌ی اتصال «{profile.database.uri_env}» نام کاربری و رمز ندارد؛ "
            "کاربر فقط-خواندنی از روی آن ساخته نمی‌شود."
        )
    target = profile.database.database
    # پایگاه احراز هویت: اگر در URI صریح نیامده، همان دیتابیسِ مسیر است.
    auth_db = parsed["options"].get("authSource") or parsed["database"] or target
    roles = [{"role": "read", "db": target}]

    try:
        client[auth_db].command("createUser", username, pwd=password, roles=roles)
        return f"کاربر فقط-خواندنی «{username}» در «{auth_db}» ساخته شد."
    except OperationFailure as exc:
        # 51003 = UserAlreadyExists؛ متن پیام هم بررسی می‌شود چون کد خطا بین
        # نسخه‌های مونگو همیشه یکسان نبوده است.
        if exc.code != 51003 and "already exists" not in str(exc):
            raise
        client[auth_db].command("updateUser", username, pwd=password, roles=roles)
        return f"کاربر فقط-خواندنی «{username}» از قبل بود و به‌روز شد."


def _verify_readonly(profile) -> str:
    """معیار پذیرش قدم: اتصال مشتری باید بخواند و **نتواند بنویسد**.

    این بررسی عمداً خودکار است؛ اگر دستی بماند روزی فراموش می‌شود و آن روز یک
    اتصال نوشتنی به تولید می‌رود.
    """
    client = MongoClient(profile.connection_uri(), serverSelectionTimeoutMS=5000)
    try:
        database = client[profile.database.database]
        readable = sorted(database.list_collection_names())
        try:
            database["__write_probe"].insert_one({"probe": True})
        except OperationFailure:
            return (
                f"اتصال فقط-خواندنی سالم است: {len(readable)} کالکشن خواند، "
                "نوشتن رد شد."
            )
        database["__write_probe"].drop()
        return "⚠ هشدار: اتصال مشتری توانست بنویسد! نقش کاربر را بررسی کنید."
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="پرکردن مونگوی توسعه از CSVها")
    parser.add_argument("customer", nargs="?", default="demo", help="نام مشتری")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "raw",
        help="پوشه‌ی CSVها (پیش‌فرض: data/raw)",
    )
    args = parser.parse_args(argv)

    try:
        profile = load_profile(args.customer)
    except ProfileError as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 1

    admin_uri = os.environ.get(ADMIN_URI_ENV)
    if not admin_uri:
        print(
            f"خطا: متغیر محیطی «{ADMIN_URI_ENV}» تعریف نشده است. "
            "`.env.example` را به `.env` کپی و مقادیرش را پر کنید.",
            file=sys.stderr,
        )
        return 1

    # نام کالکشن از نام فایل می‌آید: `<database>.<collection>.csv` — همان الگوی
    # export مونگو. فهرست سفید پروفایل مرجع است: فایلی که در پروفایل اعلام نشده
    # seed نمی‌شود، وگرنه در دیتابیس کالکشنی می‌ماند که گارد قدم ۴ اجازه‌ی
    # خواندنش را نمی‌دهد و فقط سردرگمی می‌سازد.
    allowed = profile.database.collections
    files = {p.stem.rsplit(".", 1)[-1]: p for p in sorted(args.data_dir.glob("*.csv"))}
    if not files:
        print(f"خطا: هیچ CSVی در «{args.data_dir}» نیست.", file=sys.stderr)
        return 1

    # ساخت کلاینت هم داخل try است: یک URI بدشکل همین‌جا استثنا می‌دهد و
    # اسکریپت باید پیام فارسی بدهد نه traceback.
    client: MongoClient | None = None
    try:
        client = MongoClient(admin_uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        database = client[profile.database.database]
        print(f"مشتری: {profile.name} · دیتابیس: {profile.database.database}\n")

        for collection, path in files.items():
            if allowed and collection not in allowed:
                print(f"  ⏭  {collection}: در پروفایل اعلام نشده — رد شد")
                continue
            documents, empty_columns = _load_csv(path)
            # drop پیش از insert: اسکریپت باید قابل اجرای دوباره باشد و
            # نتیجه‌اش همیشه یکی باشد، نه انباشته.
            database[collection].drop()
            if documents:
                database[collection].insert_many(documents, ordered=False)
            count = database[collection].count_documents({})
            note = (
                f" · ستون خالی (وارد نشد): {', '.join(empty_columns)}"
                if empty_columns
                else ""
            )
            print(f"  ✓  {collection}: {count} داکیومنت{note}")

        missing = [name for name in allowed if name not in files]
        if missing:
            print(f"\n  ⚠  کالکشن‌های پروفایل بدون CSV: {', '.join(missing)}")

        print("\n" + _ensure_readonly_user(client, profile))
    except ServerSelectionTimeoutError:
        # رایج‌ترین خطای این اسکریپت است و متن خام pymongo چند خط انگلیسی
        # است که علت واقعی را پنهان می‌کند.
        print(
            "\nخطا: مونگو در دسترس نیست. آیا `docker compose up -d` اجرا شده است؟",
            file=sys.stderr,
        )
        return 1
    except (PyMongoError, ProfileError) as exc:
        print(f"\nخطا در اتصال یا اجرا: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()

    try:
        print(_verify_readonly(profile))
    except (PyMongoError, ProfileError) as exc:
        print(f"بررسی اتصال فقط-خواندنی شکست خورد: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
