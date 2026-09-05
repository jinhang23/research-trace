"""Index append-only execution evidence without a second mutable run database."""

from alembic import op

revision = '0002_run_index'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_run ON events(project_id, json_extract(payload_json,'$.research_run.id'), CAST(json_extract(payload_json,'$.research_run.revision') AS INTEGER)) WHERE json_type(payload_json,'$.research_run')='object'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_attachment_capture ON attachments(project_id,target_type,target_id,json_extract(metadata_json,'$.capture_key'))"
    )


def downgrade():
    op.drop_index('idx_attachment_capture', table_name='attachments')
    op.drop_index('idx_events_run', table_name='events')
