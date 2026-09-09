"""
chestny.models — SQLAlchemy модели для приложения Честного Знака.

Все модели используют единый db из chestny.factory.
Ни одна модель не хранит PIN, private key, token или полный КИЗ.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.chestny.factory import db


class TurnoverDocument(db.Model):
    """Durable submission intent; no signed body or raw codes."""
    __tablename__ = 'turnover_document'
    id = db.Column(db.String(36), primary_key=True)
    profile_id = db.Column(db.String(50), nullable=False, index=True)
    environment = db.Column(db.String(200), nullable=False)
    inn = db.Column(db.String(12), nullable=False)
    operation = db.Column(db.String(20), nullable=False)
    state = db.Column(db.String(24), nullable=False, default='PREPARED')
    document_id = db.Column(db.String(128))
    payload_digest = db.Column(db.String(64), nullable=False)
    message = db.Column(db.String(500), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    items = db.relationship('TurnoverEvent', backref='document', lazy='select')


class TurnoverEvent(db.Model):
    """One WB event in a code's history; reservations survive process failure."""
    __tablename__ = 'turnover_event'
    id = db.Column(db.Integer, primary_key=True)
    document_key = db.Column(db.String(36), db.ForeignKey('turnover_document.id'), nullable=False)
    profile_id = db.Column(db.String(50), nullable=False)
    environment = db.Column(db.String(200), nullable=False)
    code_digest = db.Column(db.String(64), nullable=False, index=True)
    event_digest = db.Column(db.String(64), nullable=False)
    mask = db.Column(db.String(20), nullable=False)
    operation = db.Column(db.String(20), nullable=False)
    state = db.Column(db.String(24), nullable=False)
    active_key = db.Column(db.String(64), unique=True, nullable=True)
    previous_id = db.Column(db.Integer, nullable=True)
    cycle = db.Column(db.Integer, nullable=False, default=1)
    source = db.Column(db.String(12), nullable=False)
    event_date = db.Column(db.String(10), nullable=False)
    row_index = db.Column(db.Integer, nullable=False)
    paid = db.Column(db.Boolean, nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (db.UniqueConstraint('profile_id', 'environment', 'event_digest',
                                         name='uq_turnover_event'),)


class TurnoverObservation(db.Model):
    """Last independently observed code state, never a fabricated submission."""
    __tablename__ = 'turnover_observation'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.String(50), nullable=False)
    environment = db.Column(db.String(200), nullable=False)
    code_digest = db.Column(db.String(64), nullable=False)
    state = db.Column(db.String(32), nullable=False)
    decision = db.Column(db.String(32), nullable=False)
    checked_at = db.Column(db.DateTime, nullable=False)
    __table_args__ = (db.UniqueConstraint('profile_id', 'environment', 'code_digest',
                                         name='uq_turnover_observation'),)


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
    hmac_digest = db.Column(db.String(128), nullable=False)
    mask = db.Column(db.String(20), nullable=False)  # маска для UI (первые 4 + последние 4)
    profile_id = db.Column(db.String(50),
                           db.ForeignKey("organization_profile.id", ondelete="CASCADE"),
                           nullable=False)
    status = db.Column(db.String(20), nullable=False, default="PENDING")
    document_id = db.Column(db.String(100), nullable=True)
    processed_at = db.Column(db.DateTime, nullable=False,
                             default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("profile_id", "hmac_digest", name="uq_processed_kiz_profile_hmac"),
        db.Index("idx_processed_kiz_profile_hmac", "profile_id", "hmac_digest"),
    )

    def __repr__(self) -> str:
        return (
            f"ProcessedKiz(id={self.id}, mask={self.mask!r}, "
            f"status={self.status!r})"
        )
