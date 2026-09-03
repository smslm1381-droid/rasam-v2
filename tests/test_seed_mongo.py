"""تست تبدیل CSV به داکیومنت — بدون تماس با مونگو.

چرا فقط همین بخش تست می‌شود: اتصال و ساخت کاربر به یک مونگوی زنده نیاز دارند
و تستشان جای دیگری است. اما تبدیل نوع تابع خالص است و اگر بشکند، همه‌ی
قدم‌های بعدی روی داده‌ی غلط ساخته می‌شوند بی‌آنکه کسی متوجه شود.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from seed_mongo import _load_csv  # noqa: E402


def _csv(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def test_types_are_inferred_per_column(tmp_path):
    path = _csv(
        tmp_path,
        "solar_telemetry.inverter_data.csv",
        """
_id,timestamp,int_temp,device_code,ingested_at,is_reset
6a26aac12025f7b47b0d2a53,1780918908,46.5,inv1,2026-06-08T11:42:57.079Z,false
6a26aac12025f7b47b0d2a54,1780918968,47,inv1,2026-06-08T11:43:57.000Z,true
""",
    )
    docs, empty = _load_csv(path)

    assert empty == []
    assert docs[0]["_id"] == ObjectId("6a26aac12025f7b47b0d2a53")
    assert docs[0]["timestamp"] == 1780918908  # Unix Epoch باید int بماند
    assert docs[0]["ingested_at"] == datetime(
        2026, 6, 8, 11, 42, 57, 79000, tzinfo=timezone.utc
    )
    assert docs[0]["is_reset"] is False and docs[1]["is_reset"] is True
    # `47` تنهایی int است، ولی چون ستون یک مقدار اعشاری دارد کل ستون float
    # می‌شود — وگرنه یک فیلد با دو نوع در دیتابیس می‌ماند.
    assert isinstance(docs[1]["int_temp"], float)


def test_empty_columns_become_absent_fields(tmp_path):
    path = _csv(
        tmp_path,
        "solar_telemetry.mvpanel_data.csv",
        """
timestamp,fa_al_message,voltage_a,status
1780918908,,NaN,ON
1780918968,,NaN,ON
""",
    )
    docs, empty = _load_csv(path)

    assert sorted(empty) == ["fa_al_message", "voltage_a"]
    # نه None و نه رشته‌ی خالی: کلید اصلاً نباید باشد، وگرنه «درصد حضور» در
    # قدم ۳ عدد غلط می‌دهد.
    assert "fa_al_message" not in docs[0]
    assert set(docs[0]) == {"timestamp", "status"}


def test_partly_missing_column_keeps_present_values(tmp_path):
    path = _csv(
        tmp_path,
        "solar_telemetry.string_data.csv",
        """
timestamp,voltage
1780918908,12.5
1780918968,NaN
""",
    )
    docs, _ = _load_csv(path)

    assert docs[0]["voltage"] == 12.5
    assert "voltage" not in docs[1]


def test_object_id_conversion_is_limited_to_id_column(tmp_path):
    path = _csv(
        tmp_path,
        "solar_telemetry.inverter_data.csv",
        """
_id,message_id
6a26aac12025f7b47b0d2a53,6a26aac12025f7b47b0d2a99
""",
    )
    docs, _ = _load_csv(path)

    assert isinstance(docs[0]["_id"], ObjectId)
    # یک رشته‌ی ۲۴کاراکتری hex در ستون دیگر نباید سهواً ObjectId شود.
    assert docs[0]["message_id"] == "6a26aac12025f7b47b0d2a99"


def test_empty_file_is_not_an_error(tmp_path):
    path = _csv(tmp_path, "solar_telemetry.empty_data.csv", "timestamp,voltage")
    assert _load_csv(path) == ([], [])
