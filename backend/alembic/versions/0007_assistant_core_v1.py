"""add assistant core v1 persistence

Revision ID: 0007_assistant_core_v1
Revises: 0006_design_screenshot_flow
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_assistant_core_v1"
down_revision = "0006_design_screenshot_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_visitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_hash"),
    )
    op.create_index("ix_assistant_visitors_credential_hash", "assistant_visitors", ["credential_hash"], unique=True)
    op.create_table(
        "assistant_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('active', 'closed')", name="ck_assistant_conversation_status"),
        sa.ForeignKeyConstraint(["visitor_id"], ["assistant_visitors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_conversations_visitor_id", "assistant_conversations", ["visitor_id"])
    op.create_index("ix_assistant_conversations_status", "assistant_conversations", ["status"])
    op.create_index(
        "uq_assistant_active_conversation_per_visitor",
        "assistant_conversations",
        ["visitor_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("client_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page_hint", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role in ('user', 'assistant', 'system')", name="ck_assistant_message_role"),
        sa.CheckConstraint("status in ('completed', 'failed')", name="ck_assistant_message_status"),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_messages_conversation_id", "assistant_messages", ["conversation_id"])
    op.create_index("ix_assistant_messages_conversation_created", "assistant_messages", ["conversation_id", "created_at"])
    op.create_index(
        "uq_assistant_message_client_turn_role",
        "assistant_messages",
        ["conversation_id", "client_message_id", "role"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("assistant_messages")
    op.drop_table("assistant_conversations")
    op.drop_table("assistant_visitors")
