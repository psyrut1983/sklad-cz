"""
chestny.models — SQLAlchemy модели для приложения Честного Знака.

Все модели используют единый db из chestny.factory.
Ни одна модель не хранит PIN, private key, token или полный КИЗ.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.chestny.factory import db


# ═════════════════════════════════════════════════════════════════════════════
#  OrganizationProfile
# ═════════════════════════════════════════════════════════════════════════════


class OrganizationProfile(db.Model):
    """Профиль организации — стабильный immutable идентификатор."""

    __tablename__ = "organization_profile"

    id = db.Column(db.String(50), primary_key=True)  # stable UUID-like, e.g. "org-sinyavin"
    display_name = db.Column(db.String(200), unique=True, nullable=False)  # immutable
    inn = db.Column(db.String(12), nullable=True)
    certificate_thumbprint = db.Column(db.String(128), nullable=True)
    fias_id = db.Column(db.String(50), nullable=True)
    api_url = db.Column(db.String(500), nullable=True)
    product_group = db.Column(db.String(10), nullable=False, default="lp")  # fixed "lp"
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.CheckConstraint("product_group = 'lp'", name="ck_profile_product_group"),
    )

    # ── Связи ─────────────────────────────────────────────────────────────
    import_jobs = db.relationship("ImportJob", backref="profile", lazy="dynamic",
                                  cascade="all, delete-orphan")
    submission_batches = db.relationship("SubmissionBatch", backref="profile", lazy="dynamic",
                                         cascade="all, delete-orphan")
    processed_kiz = db.relationship("ProcessedKiz", backref="profile", lazy="dynamic",
                                    cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"OrganizationProfile(id={self.id!r}, "
            f"display_name={self.display_name!r})"
        )


# ═════════════════════════════════════════════════════════════════════════════
#  ImportJob
# ═════════════════════════════════════════════════════════════════════════════


class ImportJob(db.Model):
    """Задача импорта XLSX — метаданные, без raw-данных."""

    __tablename__ = "import_job"

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.String(50),
                           db.ForeignKey("organization_profile.id", ondelete="CASCADE"),
                           nullable=False)
    file_fingerprint = db.Column(db.String(128), nullable=False)
    total_rows = db.Column(db.Integer, nullable=False, default=0)
    accepted_count = db.Column(db.Integer, nullable=False, default=0)
    excluded_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="PENDING")
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # ── Связи ─────────────────────────────────────────────────────────────
    batches = db.relationship("SubmissionBatch", backref="job", lazy="dynamic",
                              cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"ImportJob(id={self.id}, profile_id={self.profile_id!r}, "
            f"status={self.status!r}, rows={self.total_rows})"
        )


# ═════════════════════════════════════════════════════════════════════════════
#  SubmissionBatch
# ═════════════════════════════════════════════════════════════════════════════


class SubmissionBatch(db.Model):
    """Пакет строк, отправленных в Честный Знак."""

    __tablename__ = "submission_batch"

    STATES = ("PENDING", "SENDING", "SENT", "CONFIRMED", "FAILED", "UNKNOWN")

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer,
                       db.ForeignKey("import_job.id", ondelete="CASCADE"),
                       nullable=False)
    profile_id = db.Column(db.String(50),
                           db.ForeignKey("organization_profile.id", ondelete="CASCADE"),
                           nullable=False)
    batch_fingerprint = db.Column(db.String(128), nullable=False)
    state = db.Column(db.String(20), nullable=False, default="PENDING")
    document_id = db.Column(db.String(100), nullable=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.Text, nullable=True)  # безопасное сообщение
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.CheckConstraint("state IN ('PENDING','SENDING','SENT','CONFIRMED','FAILED','UNKNOWN')",
                           name="ck_batch_state"),
    )

    def __repr__(self) -> str:
        return (
            f"SubmissionBatch(id={self.id}, state={self.state!r}, "
            f"attempts={self.attempts})"
        )


# ═════════════════════════════════════════════════════════════════════════════
#  ProcessedKiz
# ═════════════════════════════════════════════════════════════════════════════


class ProcessedKiz(db.Model):
    """Обработанный КИ — только HMAC-дайджест и маска, без полного КИЗ."""

    __tablename__ = "processed_kiz"

    id = db.Column(db.Integer, primary_key=True)
    hmac_digest = db.Column(db.String(128), unique=True, nullable=False)  # глобальный unique
    mask = db.Column(db.String(20), nullable=False)  # маска для UI (первые 4 + последние 4)
    profile_id = db.Column(db.String(50),
                           db.ForeignKey("organization_profile.id", ondelete="CASCADE"),
                           nullable=False)
    status = db.Column(db.String(20), nullable=False, default="PENDING")
    document_id = db.Column(db.String(100), nullable=True)
    processed_at = db.Column(db.DateTime, nullable=False,
                             default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.Index("idx_processed_kiz_hmac", "hmac_digest", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"ProcessedKiz(id={self.id}, mask={self.mask!r}, "
            f"status={self.status!r})"
        )
