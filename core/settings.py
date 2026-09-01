"""تنظیمات سراسری رصام.

چرا یک لایه‌ی جداگانه: قاعده‌ی «هیچ مسیر، کلید یا نام مدلی hard-code نمی‌شود»
تنها وقتی قابل نگهداری است که یک نقطه‌ی ورود به محیط اجرا داشته باشیم. بقیه‌ی
کد به‌جای `os.environ` از `get_settings()` استفاده می‌کند تا اگر روزی منبع
تنظیمات عوض شد (Vault، کانفیگ‌سرور، …) فقط همین فایل تغییر کند.
"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ریشه‌ی پروژه از محل همین فایل استنتاج می‌شود تا اجرای اسکریپت از هر
# دایرکتوری‌ای یک نتیجه بدهد.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# چرا load_dotenv جدا از pydantic-settings: رشته‌های اتصال مشتری‌ها پیشوند
# RASAM_ ندارند (نامشان را خود پروفایل تعیین می‌کند)، پس pydantic آن‌ها را
# نمی‌بیند. اینجا وارد os.environ می‌شوند تا `profile.connection_uri()` بتواند
# با نام دلخواه سراغشان برود. override=False یعنی متغیر واقعیِ محیط سیستم
# همیشه بر فایل `.env` اولویت دارد (رفتار درست در استقرار).
load_dotenv(PROJECT_ROOT / ".env", override=False)


class Settings(BaseSettings):
    """تنظیماتی که به کل محصول مربوط‌اند، نه به یک مشتری خاص."""

    model_config = SettingsConfigDict(
        env_prefix="RASAM_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # چرا اختیاری‌اند: قدم‌های ۱ تا ۴ (پروفایل، استنتاج schema، گارد امنیتی)
    # هیچ تماسی با مدل ندارند و نباید بدون `.env` از کار بیفتند. نبودِ این
    # مقادیر در قدم ۵ — سرِ مصرف واقعی — به خطای واضح تبدیل می‌شود، نه هنگام
    # import شدن ماژول.
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None

    # مقادیر پیش‌فرض حاصل تجربه‌ی نسخه‌ی ۱‌اند (docs/v1-reference.md بخش ۵).
    llm_max_tokens: int = Field(default=1024, gt=0)
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_disable_reasoning: bool = True

    # حالت پرگو: چاپ پرامپت کامل و پاسخ خام مدل.
    verbose: bool = False

    customers_dir: Path = Path("customers")

    @field_validator("customers_dir")
    @classmethod
    def _resolve_against_project_root(cls, value: Path) -> Path:
        """مسیر نسبی را نسبت به ریشه‌ی پروژه معنا می‌کند، نه دایرکتوری جاری."""
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """نمونه‌ی یکتای تنظیمات.

    کش می‌شود چون خواندن محیط باید یک‌بار و در یک لحظه اتفاق بیفتد؛ وگرنه دو
    بخش از سیستم ممکن است با دو تنظیمات متفاوت کار کنند. تست‌ها با
    `get_settings.cache_clear()` بازنشانی می‌کنند.
    """
    return Settings()
