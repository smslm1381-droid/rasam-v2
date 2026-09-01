"""تست‌های لایه‌ی پروفایل — بدون تماس با دیتابیس و مدل زبانی."""

import pytest
import yaml

from core.profiles import ProfileError, load_profile


def _write(tmp_path, name, data):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "profile.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


VALID = {
    "name": "acme",
    "database": {"type": "MongoDB", "uri_env": "ACME_MONGO_URI", "database": "solar"},
}


def test_demo_profile_loads():
    profile = load_profile("demo")
    assert profile.database.type == "mongodb"
    assert profile.capabilities.chart is True
    assert "inverter_data" in profile.database.collections
    assert profile.vector_store_dir.name == "vector_store"


def test_defaults_are_conservative(tmp_path):
    profile = load_profile("acme", _write(tmp_path, "acme", VALID))
    # قابلیتی که خریداری نشده نباید سهواً روشن باشد.
    assert profile.capabilities.summary is False
    assert profile.limits.max_rows == 1000


def test_missing_required_field_reports_field_name(tmp_path):
    broken = {"name": "acme", "database": {"type": "mongodb", "database": "solar"}}
    with pytest.raises(ProfileError, match="uri_env"):
        load_profile("acme", _write(tmp_path, "acme", broken))


def test_connection_string_inside_profile_is_rejected(tmp_path):
    leaky = {
        "name": "acme",
        "database": {
            "type": "mongodb",
            "uri_env": "mongodb://user:pass@localhost:27017",
            "database": "solar",
        },
    }
    with pytest.raises(ProfileError, match="نام متغیر محیطی"):
        load_profile("acme", _write(tmp_path, "acme", leaky))


def test_unknown_capability_is_rejected(tmp_path):
    typo = dict(VALID, capabilities={"tabel": True})
    with pytest.raises(ProfileError, match="tabel"):
        load_profile("acme", _write(tmp_path, "acme", typo))


@pytest.mark.parametrize("name", ["../secrets", "de mo", "Demo/x", ""])
def test_path_traversal_names_are_rejected(name):
    with pytest.raises(ProfileError, match="نام مشتری نامعتبر"):
        load_profile(name)


def test_missing_env_var_gives_clear_error(monkeypatch):
    monkeypatch.delenv("DEMO_MONGO_URI", raising=False)
    with pytest.raises(ProfileError, match="DEMO_MONGO_URI"):
        load_profile("demo").connection_uri()
