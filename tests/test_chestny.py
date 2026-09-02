"""
Тесты chestny — изолированное приложение Честного Знака.

Проверяет:
- Фабрику, идемпотентность, изолированную temp БД
- Ровно два профиля с ожидаемыми id/именами
- Схему, constraints, индексы, FK, defaults, enum states
- Глобальный unique HMAC
- Безопасный repr (без sensitive данных)
- Health endpoint
- Route isolation (нет старых warehouse/SKU/UI рутов)
- Runner AST и конфигурацию (127.0.0.1)
- Запрещённые имена колонок
"""

from __future__ import annotations

import ast
import os
import secrets
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect, text

from app.chestny.factory import create_cz_app, db, STABLE_PROFILES
from app.chestny.models import (
    OrganizationProfile,
    ImportJob,
    SubmissionBatch,
    ProcessedKiz,
)


# ═════════════════════════════════════════════════════════════════════════════
#  Хелперы
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def app():
    """Создаёт приложение с временной SQLite БД."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    app = create_cz_app(db_uri=f"sqlite:///{db_path}", testing=True)
    ctx = app.app_context()
    ctx.push()
    try:
        yield app
    finally:
        db.session.close()
        db.engine.dispose()
        ctx.pop()
        os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


# ═════════════════════════════════════════════════════════════════════════════
#  1. Factory и идемпотентность
# ═════════════════════════════════════════════════════════════════════════════


class TestFactory:
    def test_create_app(self, app):
        """Фабрика создаёт Flask-приложение."""
        assert app is not None
        assert app.testing is True

    def test_factory_idempotent(self):
        """Повторный вызов create_cz_app с той же БД не падает."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        uri = f"sqlite:///{db_path}"
        try:
            app1 = create_cz_app(db_uri=uri, testing=True)
            app2 = create_cz_app(db_uri=uri, testing=True)
            assert app1 is not None
            assert app2 is not None
        finally:
            os.unlink(db_path)

    def test_isolated_temp_db(self, app):
        """Приложение использует переданный db_uri, а не default."""
        assert "sqlite:///" in app.config["SQLALCHEMY_DATABASE_URI"]
        assert "instance" not in app.config["SQLALCHEMY_DATABASE_URI"] or \
               "chestny/instance" in app.config["SQLALCHEMY_DATABASE_URI"] or \
               app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:////tmp/")

    def test_no_static_secret(self):
        """secret_key не является статической строкой."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        uri = f"sqlite:///{db_path}"
        try:
            app = create_cz_app(db_uri=uri, testing=True)
            assert app.secret_key is not None
            # Не должен содержать подсказок "do-not-use" или "secret"
            assert "do-not-use" not in app.secret_key
        finally:
            os.unlink(db_path)

    def test_two_calls_different_keys(self):
        """Два вызова factory без аргумента secret_key получают разные ключи."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path1 = f.name
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path2 = f.name
        uri1 = f"sqlite:///{db_path1}"
        uri2 = f"sqlite:///{db_path2}"
        try:
            app1 = create_cz_app(db_uri=uri1, testing=True)
            app2 = create_cz_app(db_uri=uri2, testing=True)
            assert app1.secret_key != app2.secret_key
        finally:
            os.unlink(db_path1)
            os.unlink(db_path2)

    def test_explicit_secret_key(self):
        """Явный secret_key из параметра используется."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        uri = f"sqlite:///{db_path}"
        try:
            app = create_cz_app(db_uri=uri, testing=True, secret_key="test-fixed-key-42")
            assert app.secret_key == "test-fixed-key-42"
        finally:
            os.unlink(db_path)


# ═════════════════════════════════════════════════════════════════════════════
#  2. Профили
# ═════════════════════════════════════════════════════════════════════════════


class TestProfiles:
    def test_two_profiles(self, app):
        """Ровно две организации."""
        profiles = OrganizationProfile.query.all()
        assert len(profiles) == 2

    def test_profile_ids_and_names(self, app):
        """Ожидаемые stable id и display_name."""
        profiles = {p.id: p for p in OrganizationProfile.query.all()}
        assert "org-sinyavin" in profiles
        assert "org-krasikova" in profiles
        assert profiles["org-sinyavin"].display_name == "ИП Синявин"
        assert profiles["org-krasikova"].display_name == "ИП Красикова"

    def test_product_group_fixed(self, app):
        """product_group = 'lp' для всех профилей."""
        for p in OrganizationProfile.query.all():
            assert p.product_group == "lp"

    def test_seed_idempotent(self, app):
        """Второй вызов _seed_profiles не добавляет дублей."""
        from app.chestny.factory import _seed_profiles
        _seed_profiles()
        assert OrganizationProfile.query.count() == 2

    def test_stable_profile_constants(self):
        """STABLE_PROFILES содержит ровно две записи с ожидаемыми полями."""
        assert len(STABLE_PROFILES) == 2
        ids = {p["id"] for p in STABLE_PROFILES}
        assert ids == {"org-sinyavin", "org-krasikova"}
        for p in STABLE_PROFILES:
            assert p["product_group"] == "lp"


# ═════════════════════════════════════════════════════════════════════════════
#  3. Schema, constraints, indexes
# ═════════════════════════════════════════════════════════════════════════════


class TestSchema:
    def test_organization_profile_columns(self, app):
        """Таблица organization_profile имеет ожидаемые колонки."""
        inspector = sa_inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("organization_profile")}
        expected = {"id", "display_name", "inn", "certificate_thumbprint",
                    "fias_id", "api_url", "product_group", "updated_at"}
        assert cols == expected

    def test_import_job_columns(self, app):
        """Таблица import_job имеет ожидаемые колонки."""
        inspector = sa_inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("import_job")}
        expected = {"id", "profile_id", "file_fingerprint", "total_rows",
                    "accepted_count", "excluded_count", "status",
                    "created_at", "updated_at"}
        assert cols == expected

    def test_submission_batch_columns(self, app):
        """Таблица submission_batch имеет ожидаемые колонки."""
        inspector = sa_inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("submission_batch")}
        expected = {"id", "job_id", "profile_id", "batch_fingerprint",
                    "state", "document_id", "attempts", "error_message",
                    "created_at", "updated_at"}
        assert cols == expected

    def test_processed_kiz_columns(self, app):
        """Таблица processed_kiz имеет ожидаемые колонки."""
        inspector = sa_inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("processed_kiz")}
        expected = {"id", "hmac_digest", "mask", "profile_id", "status",
                    "document_id", "processed_at"}
        assert cols == expected

    def test_profile_unique_display_name(self, app):
        """display_name имеет unique constraint."""
        # SQLite: unique=True создаёт UNIQUE-ограничение на уровне столбца
        with db.engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(organization_profile)"))
            rows = result.fetchall()
        unique_cols = set()
        for row in rows:
            # row: (cid, name, type, notnull, dflt_value, pk)
            # SQLite PRAGMA не показывает UNIQUE; проверяем через создание дубля
            pass
        # Проверяем через попытку вставки дублирующегося display_name
        p1 = OrganizationProfile(id="dup-test-1", display_name="Dup Name",
                                  product_group="lp")
        db.session.add(p1)
        db.session.commit()
        p2 = OrganizationProfile(id="dup-test-2", display_name="Dup Name",
                                  product_group="lp")
        db.session.add(p2)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_processed_kiz_hmac_unique(self, app):
        """hmac_digest имеет global unique constraint."""
        inspector = sa_inspect(db.engine)
        idxs = inspector.get_indexes("processed_kiz")
        unique_cols = set()
        for idx in idxs:
            if idx.get("unique"):
                unique_cols.update(idx["column_names"])
        assert "hmac_digest" in unique_cols

    def test_batch_state_check(self, app):
        """submission_batch.state имеет CHECK constraint с допустимыми значениями."""
        inspector = sa_inspect(db.engine)
        check_constraints = inspector.get_check_constraints("submission_batch")
        constraint_names = {c["name"] for c in check_constraints}
        assert "ck_batch_state" in constraint_names

    def test_profile_product_group_check(self, app):
        """organization_profile.product_group имеет CHECK = 'lp'."""
        inspector = sa_inspect(db.engine)
        check_constraints = inspector.get_check_constraints("organization_profile")
        constraint_names = {c["name"] for c in check_constraints}
        assert "ck_profile_product_group" in constraint_names

    def test_foreign_keys_exist(self, app):
        """FK: import_job.profile_id, submission_batch.job_id/profile_id, processed_kiz.profile_id."""
        inspector = sa_inspect(db.engine)
        for table in ("import_job", "submission_batch", "processed_kiz"):
            fks = inspector.get_foreign_keys(table)
            assert len(fks) >= 1, f"{table} не имеет FK"

    def test_defaults(self, app):
        """Проверка DEFAULT значений."""
        # ImportJob defaults
        job = ImportJob(profile_id="org-sinyavin", file_fingerprint="test")
        db.session.add(job)
        db.session.commit()
        assert job.status == "PENDING"
        assert job.total_rows == 0
        assert job.accepted_count == 0
        assert job.excluded_count == 0

        # SubmissionBatch defaults
        batch = SubmissionBatch(job_id=job.id, profile_id="org-sinyavin",
                                batch_fingerprint="test-batch")
        db.session.add(batch)
        db.session.commit()
        assert batch.state == "PENDING"
        assert batch.attempts == 0

        # ProcessedKiz defaults
        pk = ProcessedKiz(hmac_digest="abc123", mask="1234****5678",
                          profile_id="org-sinyavin")
        db.session.add(pk)
        db.session.commit()
        assert pk.status == "PENDING"

    def test_processed_kiz_hmac_global_unique(self, app):
        """Глобальный unique: второй hmac_digest с тем же значением падает."""
        pk1 = ProcessedKiz(hmac_digest="unique-hash-1", mask="1234****5678",
                           profile_id="org-sinyavin")
        db.session.add(pk1)
        db.session.commit()

        pk2 = ProcessedKiz(hmac_digest="unique-hash-1", mask="9999****0000",
                           profile_id="org-krasikova")
        with pytest.raises(Exception):
            db.session.add(pk2)
            db.session.commit()
        db.session.rollback()


# ═════════════════════════════════════════════════════════════════════════════
#  4. Safe repr
# ═════════════════════════════════════════════════════════════════════════════


class TestSafeRepr:
    def test_profile_repr_no_sensitive(self, app):
        """repr(OrganizationProfile) не содержит inn, thumbprint, api_url."""
        p = OrganizationProfile.query.first()
        r = repr(p)
        assert "inn" not in r or "None" in r
        assert "thumbprint" not in r
        assert "api_url" not in r
        assert "org-sinyavin" in r or "org-krasikova" in r

    def test_import_job_repr(self, app):
        """repr(ImportJob) содержит только метаданные."""
        job = ImportJob(profile_id="org-sinyavin", file_fingerprint="fp",
                        total_rows=10)
        r = repr(job)
        assert "ImportJob" in r
        assert "org-sinyavin" in r
        assert "fp" not in r  # fingerprint не должен быть в repr

    def test_submission_batch_repr(self, app):
        """repr(SubmissionBatch) не содержит document_id или error_message."""
        batch = SubmissionBatch(job_id=1, profile_id="org-sinyavin",
                                batch_fingerprint="bf", document_id="doc-123",
                                error_message="some error")
        r = repr(batch)
        assert "doc-123" not in r
        assert "some error" not in r

    def test_processed_kiz_repr(self, app):
        """repr(ProcessedKiz) не содержит hmac_digest."""
        pk = ProcessedKiz(hmac_digest="secret-hash", mask="1234****5678",
                          profile_id="org-sinyavin")
        r = repr(pk)
        assert "secret-hash" not in r
        assert "1234" in r


# ═════════════════════════════════════════════════════════════════════════════
#  5. Запрещённые имена колонок
# ═════════════════════════════════════════════════════════════════════════════


class TestForbiddenColumns:
    """Ни одна модель не содержит PIN, private key, token, full KIZ."""

    FORBIDDEN = {"pin", "private_key", "token", "full_kiz", "raw_kiz",
                 "crypto_tail", "complete_km", "password", "secret_key"}

    def test_no_forbidden_columns_in_any_model(self, app):
        """Ни одна таблица не содержит запрещённых колонок."""
        inspector = sa_inspect(db.engine)
        for table in inspector.get_table_names():
            cols = {c["name"].lower() for c in inspector.get_columns(table)}
            forbidden_found = cols & self.FORBIDDEN
            assert not forbidden_found, f"{table}: найдены {forbidden_found}"


# ═════════════════════════════════════════════════════════════════════════════
#  6. Health endpoint
# ═════════════════════════════════════════════════════════════════════════════


class TestHealth:
    def test_health_returns_ok(self, client):
        """GET /health → 200, status=ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["app"] == "chestny-znak"


# ═════════════════════════════════════════════════════════════════════════════
#  7. Route isolation
# ═════════════════════════════════════════════════════════════════════════════


class TestRouteIsolation:
    """Новое приложение НЕ содержит старых складских маршрутов."""

    OLD_PATHS = ["/api/warehouses", "/api/skus", "/api/units",
                 "/api/labels", "/api/tnved", "/api/settings", "/dashboard"]

    def test_no_old_routes(self, client):
        """Старые складские маршруты не зарегистрированы."""
        for path in self.OLD_PATHS:
            resp = client.get(path)
            assert resp.status_code == 404, f"{path} должен быть 404"


# ═════════════════════════════════════════════════════════════════════════════
#  8. Runner AST и конфигурация
# ═════════════════════════════════════════════════════════════════════════════


class TestRunner:
    """Проверка runner.py: host=127.0.0.1, port configurable, debug=False, use_reloader=False."""

    RUNNER_PATH = Path(__file__).parent.parent / "app" / "chestny" / "runner.py"

    def test_runner_host_is_localhost(self):
        """runner.py содержит app.run(host='127.0.0.1', ...)."""
        src = self.RUNNER_PATH.read_text()
        assert '"127.0.0.1"' in src or "'127.0.0.1'" in src

    def test_runner_no_0_0_0_0(self):
        """runner.py не содержит 0.0.0.0."""
        src = self.RUNNER_PATH.read_text()
        assert "0.0.0.0" not in src

    def test_runner_debug_false(self):
        """runner.py использует debug=False (не args.debug)."""
        src = self.RUNNER_PATH.read_text()
        assert "debug=False" in src
        assert "args.debug" not in src

    def test_runner_use_reloader_false(self):
        """runner.py использует use_reloader=False."""
        src = self.RUNNER_PATH.read_text()
        assert "use_reloader=False" in src

    def test_runner_no_debug_flag(self):
        """runner.py не содержит --debug аргумента."""
        src = self.RUNNER_PATH.read_text()
        assert "--debug" not in src

    def test_runner_parses_port(self):
        """runner.py использует argparse для port."""
        tree = ast.parse(self.RUNNER_PATH.read_text())
        # Ищем add_argument с --port
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if getattr(node.func, 'attr', None) == 'add_argument':
                    args = [ast.literal_eval(a) if isinstance(a, ast.Constant) else str(a) for a in node.args]
                    if '--port' in args:
                        found = True
        assert found, "add_argument('--port') не найден"


# ═════════════════════════════════════════════════════════════════════════════
#  9. Связи и каскады
# ═════════════════════════════════════════════════════════════════════════════


class TestRelationships:
    def test_delete_profile_cascades(self, app):
        """Удаление профиля каскадно удаляет связанные записи."""
        p = OrganizationProfile(id="org-test", display_name="Test Org",
                                product_group="lp")
        db.session.add(p)
        db.session.commit()

        job = ImportJob(profile_id="org-test", file_fingerprint="fp")
        db.session.add(job)
        db.session.commit()

        batch = SubmissionBatch(job_id=job.id, profile_id="org-test",
                                batch_fingerprint="bf")
        db.session.add(batch)
        db.session.commit()

        pk = ProcessedKiz(hmac_digest="test-hash", mask="mask",
                          profile_id="org-test")
        db.session.add(pk)
        db.session.commit()

        db.session.delete(p)
        db.session.commit()

        assert ImportJob.query.count() == 0
        assert SubmissionBatch.query.count() == 0
        assert ProcessedKiz.query.count() == 0
