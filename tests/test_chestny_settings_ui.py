"""
Тесты UI настроек ЧЗ.
Изолированная БД + тестовый клиент новой factory.
"""

from __future__ import annotations

import os
import tempfile
import pytest

from app.chestny.factory import create_cz_app, db


@pytest.fixture
def app():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    app = create_cz_app(db_uri=f"sqlite:///{db_path}", testing=True)
    ctx = app.app_context()
    ctx.push()
    yield app
    db.session.close()
    db.engine.dispose()
    ctx.pop()
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


class TestSettingsUI:
    def test_root_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_root_html_utf8(self, client):
        resp = client.get("/")
        assert resp.mimetype == "text/html"
        assert "charset" in resp.content_type.lower() or "utf-8" in resp.content_type.lower()

    def test_profile_names_present(self, client):
        html = client.get("/").data.decode()
        assert "ИП Синявин" in html
        assert "ИП Красикова" in html

    def test_profile_buttons(self, client):
        html = client.get("/").data.decode()
        assert 'data-profile="org-sinyavin"' in html
        assert 'data-profile="org-krasikova"' in html

    def test_form_fields(self, client):
        html = client.get("/").data.decode()
        for field in ("inn", "fias_id", "certificate_thumbprint"):
            assert f'name="{field}"' in html

    def test_buttons_present(self, client):
        html = client.get("/").data.decode()
        for btn in ("Сохранить настройки", "Обновить сертификаты", "Проверить сертификат"):
            assert btn in html

    def test_disabled_excel_section(self, client):
        html = client.get("/").data.decode()
        assert "Загрузка Excel" in html
        assert "Доступно после настройки профиля" in html

    def test_no_old_warehouse(self, client):
        html = client.get("/").data.decode()
        for term in ("склад", "SKU", "ТН ВЭД", "этикетк", "SSH", "sync", "маршрут"):
            assert term.lower() not in html.lower()

    def test_no_cdn_or_external(self, client):
        html = client.get("/").data.decode()
        assert "http://" not in html
        assert "https://" not in html
        assert "cdn." not in html.lower()

    def test_no_inline_handlers(self, client):
        html = client.get("/").data.decode()
        for attr in ("onclick", "onchange", "onsubmit", "onload", "eval(", "innerHTML"):
            assert attr not in html.lower()

    def test_css_local_200(self, client):
        resp = client.get("/static/chestny/settings.css")
        assert resp.status_code == 200
        assert resp.mimetype in ("text/css", "text/plain")

    def test_no_inline_secrets(self, client):
        html = client.get("/").data.decode()
        for secret in ("123456789012", "AABBCCDDEE"):
            assert secret not in html

    def test_health_still_works(self, client):
        assert client.get("/health").status_code == 200

    def test_api_still_works(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 2

    def test_product_group_readonly(self, client):
        html = client.get("/").data.decode()
        assert "Лёгкая промышленность" in html
        assert "lp" in html

    def test_pin_warning(self, client):
        html = client.get("/").data.decode()
        assert "PIN" in html and "закрытые ключи" in html


class TestDryRunUI:
    """Тесты разметки dry-run секции."""

    def test_upload_section_present(self, client):
        html = client.get("/").data.decode()
        assert 'id="upload-section"' in html
        assert 'id="file-input"' in html
        assert 'id="upload-btn"' in html
        assert 'id="cancel-import-btn"' in html
        assert 'id="submit-cz-btn"' in html

    def test_upload_section_accept_xlsx(self, client):
        html = client.get("/").data.decode()
        assert 'accept=".xlsx"' in html

    def test_upload_button_text(self, client):
        html = client.get("/").data.decode()
        assert "Проверить файл" in html

    def test_cancel_button_text(self, client):
        html = client.get("/").data.decode()
        assert "Отменить импорт" in html

    def test_submit_cz_disabled_with_note(self, client):
        html = client.get("/").data.decode()
        assert 'id="submit-cz-btn"' in html
        assert "disabled" in html
        assert "Отправка в Честный Знак" in html
        assert "будет доступна позже" in html

    def test_gate_note_present(self, client):
        html = client.get("/").data.decode()
        assert 'id="gate-note"' in html
        assert "Доступно после настройки профиля" in html

    def test_dryrun_results_container(self, client):
        html = client.get("/").data.decode()
        assert 'id="dryrun-results"' in html
        assert 'id="dryrun-summary"' in html
        assert 'id="dryrun-tables"' in html

    def test_busy_indicator(self, client):
        html = client.get("/").data.decode()
        assert 'id="upload-busy"' in html
        assert "Проверка файла" in html

    def test_upload_error_container(self, client):
        html = client.get("/").data.decode()
        assert 'id="upload-error"' in html

    def test_no_innerhtml_in_markup(self, client):
        html = client.get("/").data.decode()
        assert "innerHTML" not in html

    def test_no_storage_in_html(self, client):
        html = client.get("/").data.decode()
        for term in ("localStorage", "sessionStorage", "document.cookie"):
            assert term not in html

    def test_upload_section_starts_disabled(self, client):
        html = client.get("/").data.decode()
        # upload-controls hidden by default, gate-note visible
        assert 'id="upload-controls" style="display:none"' in html or 'style="display:none"' in html
        assert 'id="gate-note"' in html
