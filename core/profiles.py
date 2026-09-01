"""بارگذاری و اعتبارسنجی پروفایل مشتری.

پروفایل تنها چیزی است که بین دو مشتری فرق می‌کند: نوع دیتابیس، محل داده،
قابلیت‌های خریداری‌شده و سقف‌ها. کد مشترک است و هیچ‌جا نباید نام مشتری بداند.

سه تصمیم امنیتی در همین فایل زندگی می‌کنند:
۱. نام مشتری از بیرون می‌آید (در قدم ۱۳ از درخواست HTTP)، پس پیش از تبدیل‌شدن
   به مسیر فایل باید محدود شود، وگرنه `../../etc` هم یک «مشتری» است.
۲. رشته‌ی اتصال هرگز داخل پروفایل نوشته نمی‌شود — فقط *نام* متغیر محیطی. این
   قاعده اینجا اجرا می‌شود، نه صرفاً توصیه.
۳. فهرست کالکشن‌های مجاز بخشی از پروفایل است، چون گارد قدم ۴ باید بداند
   `$lookup` به کجا مجاز است. جای این دانش کانفیگ است، نه کد.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.settings import get_settings

# نام مشتری = نام پوشه. فقط حروف کوچک، رقم، خط تیره و زیرخط؛ بدون نقطه و
# اسلش، تا هیچ ورودی‌ای نتواند از پوشه‌ی customers بیرون بزند.
_SAFE_CUSTOMER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ENV_VAR_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# ترجمه‌ی رایج‌ترین خطاهای Pydantic. پیام خطای پروفایل را آدمِ فارسی‌زبان
# می‌خواند، پس نباید انگلیسی و فنی باشد.
_FA_ERRORS = {
    "missing": "الزامی است ولی در پروفایل نیامده",
    "extra_forbidden": "شناخته‌شده نیست (احتمالاً غلط املایی)",
    "string_type": "باید متن باشد",
    "int_type": "باید عدد صحیح باشد",
    "int_parsing": "باید عدد صحیح باشد",
    "bool_type": "باید true یا false باشد",
    "bool_parsing": "باید true یا false باشد",
    "greater_than": "باید بزرگ‌تر از صفر باشد",
    "list_type": "باید فهرست باشد",
}


class ProfileError(Exception):
    """خطای خواندن یا اعتبارسنجی پروفایل؛ پیامش فارسی و رو به کاربر است."""


class DatabaseConfig(BaseModel):
    """مشخصات دیتابیس مشتری. `type` تعیین می‌کند کدام ایجنت بارگذاری شود."""

    model_config = ConfigDict(extra="forbid")

    type: str
    uri_env: str
    database: str
    # خالی یعنی «هنوز محدود نشده»؛ گارد قدم ۴ در این حالت به کالکشن‌های
    # کشف‌شده در استنتاج schema اکتفا می‌کند.
    collections: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _known_shape(cls, value: str) -> str:
        # عمداً فهرست دیتابیس‌های مجاز اینجا نیست: رجیستری ایجنت‌ها (قدم ۷)
        # مرجع است. اینجا فقط شکل مقدار بررسی می‌شود تا نام‌های hard-code نشوند.
        if not value.strip():
            raise ValueError("نوع دیتابیس نمی‌تواند خالی باشد")
        return value.strip().lower()

    @field_validator("uri_env")
    @classmethod
    def _is_env_name_not_secret(cls, value: str) -> str:
        if "://" in value or "@" in value:
            raise ValueError(
                "باید «نام متغیر محیطی» باشد، نه خودِ رشته‌ی اتصال. "
                "رمز هرگز داخل profile.yaml نوشته نمی‌شود"
            )
        if not _ENV_VAR_NAME.match(value):
            raise ValueError(
                "نام متغیر محیطی نامعتبر است؛ فقط حروف بزرگ، رقم و زیرخط "
                "(مثل DEMO_MONGO_URI)"
            )
        return value


class Capabilities(BaseModel):
    """قابلیت‌های خریداری‌شده‌ی مشتری؛ در قدم ۱۱ شکل گراف را تعیین می‌کنند.

    پیش‌فرضْ حداقلی است: چیزی که مشتری نخریده، نباید سهواً روشن بماند.
    """

    model_config = ConfigDict(extra="forbid")

    table: bool = True
    summary: bool = False
    chart: bool = False
    export_csv: bool = False


class Limits(BaseModel):
    """سقف‌های اجرا. نبودشان در نسخه‌ی ۱ یک بدهی واقعی بود."""

    model_config = ConfigDict(extra="forbid")

    max_rows: int = Field(default=1000, gt=0)
    query_timeout_seconds: int = Field(default=15, gt=0)


class CustomerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    database: DatabaseConfig
    capabilities: Capabilities = Field(default_factory=Capabilities)
    limits: Limits = Field(default_factory=Limits)
    language: str = "fa"
    # مسیر پوشه‌ی مشتری؛ لودر پرش می‌کند، در YAML نمی‌آید.
    root: Path

    @property
    def knowledge_dir(self) -> Path:
        return self.root / "knowledge"

    @property
    def vector_store_dir(self) -> Path:
        return self.root / "vector_store"

    def connection_uri(self) -> str:
        """رشته‌ی اتصال را از محیط می‌خواند.

        دیرهنگام و در لحظه‌ی مصرف خوانده می‌شود تا صرفِ بارگذاری پروفایل — که
        در قدم‌های تست و ابزارها زیاد اتفاق می‌افتد — به داشتن رمزها گره نخورد.
        """
        uri = os.environ.get(self.database.uri_env)
        if not uri:
            raise ProfileError(
                f"متغیر محیطی «{self.database.uri_env}» تعریف نشده است؛ "
                f"رشته‌ی اتصال مشتری «{self.name}» در دسترس نیست."
            )
        return uri


def _fa_validation_message(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        field = " → ".join(str(part) for part in err["loc"]) or "پروفایل"
        if err["type"] == "value_error":
            detail = str(err.get("ctx", {}).get("error", err["msg"]))
        else:
            detail = _FA_ERRORS.get(err["type"], err["msg"])
        lines.append(f"  - «{field}»: {detail}")
    return "\n".join(lines)


def load_profile(name: str, customers_dir: Path | None = None) -> CustomerProfile:
    """پروفایل مشتری را می‌خواند و اعتبارسنجی می‌کند.

    در صورت هر مشکلی `ProfileError` با پیام فارسی پرتاب می‌شود. این تابع عمداً
    خطا پرتاب می‌کند و `Answer` برنمی‌گرداند: پروفایلِ خراب یک خطای پیکربندی
    است، نه یک پاسخ به سوال کاربر. تبدیلش به `Answer` کار گره‌ی گراف در قدم ۸ است.
    """
    if not isinstance(name, str) or not _SAFE_CUSTOMER_NAME.match(name):
        raise ProfileError(
            f"نام مشتری نامعتبر است: «{name}». فقط حروف کوچک انگلیسی، رقم، "
            "خط تیره و زیرخط مجاز است."
        )

    base = customers_dir or get_settings().customers_dir
    root = Path(base) / name
    path = root / "profile.yaml"
    if not path.is_file():
        raise ProfileError(f"پروفایل مشتری «{name}» پیدا نشد: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"فایل پروفایل «{name}» YAML معتبری نیست:\n{exc}") from exc


    if not isinstance(raw, dict):
        raise ProfileError(f"محتوای پروفایل «{name}» باید یک نگاشت (key: value) باشد.")

    try:
        profile = CustomerProfile(**raw, root=root)
    except ValidationError as exc:
        raise ProfileError(
            f"پروفایل مشتری «{name}» معتبر نیست:\n{_fa_validation_message(exc)}"
        ) from exc

    if profile.name != name:
        raise ProfileError(
            f"نام داخل پروفایل («{profile.name}») با نام پوشه («{name}») یکی نیست."
        )
    return profile
