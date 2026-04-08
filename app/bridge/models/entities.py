"""Modelos SQLAlchemy para cache e histórico."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bridge.models.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    serial: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    access_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    status_cache: Mapped[Optional["PrinterStatusCache"]] = relationship(
        back_populates="printer", uselist=False
    )
    ams_cache: Mapped[Optional["PrinterAmsCache"]] = relationship(
        back_populates="printer", uselist=False
    )


class PrinterStatusCache(Base):
    __tablename__ = "printer_status_cache"
    __table_args__ = (UniqueConstraint("printer_id", name="uq_status_printer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    print_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    progress_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    job_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    eta_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_layer: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_layers: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    nozzle_temp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bed_temp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    network_signal: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    printer: Mapped["Printer"] = relationship(back_populates="status_cache")


class PrinterAmsCache(Base):
    __tablename__ = "printer_ams_cache"
    __table_args__ = (UniqueConstraint("printer_id", name="uq_ams_printer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), nullable=False)
    has_ams: Mapped[bool] = mapped_column(Boolean, default=False)
    active_slot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    printer: Mapped["Printer"] = relationship(back_populates="ams_cache")


class PrinterSnapshot(Base):
    __tablename__ = "printer_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterStatusHistory(Base):
    __tablename__ = "printer_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), index=True)
    state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    print_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    progress_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    job_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    nozzle_temp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bed_temp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
