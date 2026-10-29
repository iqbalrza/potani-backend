"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-29 08:56:42.809284
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import app.db.types

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agent_logs',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('feature', sa.String(length=4), nullable=False),
    sa.Column('step', sa.String(length=64), nullable=False),
    sa.Column('tool', sa.String(length=64), nullable=False),
    sa.Column('args', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('llm_model', sa.String(length=64), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('pot_id', sa.Uuid(), nullable=True),
    sa.Column('plan_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_logs_plan_id'), ['plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_logs_pot_id'), ['pot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_logs_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_logs_user_id'), ['user_id'], unique=False)

    op.create_table('users',
    sa.Column('firebase_uid', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=True),
    sa.Column('location_lat', sa.Float(), nullable=True),
    sa.Column('location_lon', sa.Float(), nullable=True),
    sa.Column('light_default', sa.String(length=16), nullable=True),
    sa.Column('container_default', sa.String(length=64), nullable=True),
    sa.Column('materials_owned', sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), 'postgresql'), nullable=False),
    sa.Column('fcm_token', sa.Text(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_firebase_uid'), ['firebase_uid'], unique=True)

    op.create_table('devices',
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=True),
    sa.Column('fw_version', sa.String(length=32), nullable=True),
    sa.Column('battery', sa.Float(), nullable=True),
    sa.Column('last_seen_at', app.db.types.UTCDateTime(), nullable=True),
    sa.Column('online', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_devices_device_id'), ['device_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_devices_owner_id'), ['owner_id'], unique=False)

    op.create_table('pots',
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('container_type', sa.String(length=64), nullable=True),
    sa.Column('light_level', sa.String(length=16), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('needs_help', sa.Boolean(), nullable=False),
    sa.Column('help_reason', sa.Text(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('pots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pots_owner_id'), ['owner_id'], unique=False)

    op.create_table('chat_messages',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('pot_id', sa.Uuid(), nullable=True),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tool_calls', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['pot_id'], ['pots.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_messages_user_id'), ['user_id'], unique=False)

    op.create_table('plantings',
    sa.Column('pot_id', sa.Uuid(), nullable=False),
    sa.Column('crop_id', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('planted_at', app.db.types.UTCDateTime(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('outcome_note', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['pot_id'], ['pots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('plantings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_plantings_pot_id'), ['pot_id'], unique=False)

    op.create_table('sessions',
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('pot_id', sa.Uuid(), nullable=False),
    sa.Column('stage', sa.String(length=24), nullable=False),
    sa.Column('opened_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('expires_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('closed_at', app.db.types.UTCDateTime(), nullable=True),
    sa.Column('measurement_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ),
    sa.ForeignKeyConstraint(['pot_id'], ['pots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sessions_device_id'), ['device_id'], unique=False)

    op.create_table('measurements',
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('pot_id', sa.Uuid(), nullable=True),
    sa.Column('planting_id', sa.Uuid(), nullable=True),
    sa.Column('session_id', sa.Uuid(), nullable=True),
    sa.Column('stage', sa.String(length=24), nullable=True),
    sa.Column('ph', sa.Float(), nullable=True),
    sa.Column('moisture', sa.Float(), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('soil_score', sa.Float(), nullable=True),
    sa.Column('raw', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('measured_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('client_measurement_id', sa.String(length=64), nullable=True),
    sa.Column('invalid', sa.Boolean(), nullable=False),
    sa.Column('invalid_reason', sa.Text(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ),
    sa.ForeignKeyConstraint(['planting_id'], ['plantings.id'], ),
    sa.ForeignKeyConstraint(['pot_id'], ['pots.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_measurement_id')
    )
    with op.batch_alter_table('measurements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_measurements_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_measurements_measured_at'), ['measured_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_measurements_pot_id'), ['pot_id'], unique=False)

    op.create_table('plans',
    sa.Column('pot_id', sa.Uuid(), nullable=False),
    sa.Column('planting_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('crop_id', sa.String(length=64), nullable=True),
    sa.Column('goal', sa.Text(), nullable=False),
    sa.Column('steps', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('targets', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('replan_count', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('history', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('generated_by', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['planting_id'], ['plantings.id'], ),
    sa.ForeignKeyConstraint(['pot_id'], ['pots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_plans_pot_id'), ['pot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_plans_state'), ['state'], unique=False)

    op.create_table('agent_questions',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('feature', sa.String(length=4), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('pot_id', sa.Uuid(), nullable=True),
    sa.Column('plan_id', sa.Uuid(), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('options', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('field', sa.String(length=32), nullable=True),
    sa.Column('context', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['pot_id'], ['pots.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_questions_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_questions_user_id'), ['user_id'], unique=False)

    op.create_table('checkpoints',
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('pot_id', sa.Uuid(), nullable=False),
    sa.Column('param', sa.String(length=16), nullable=False),
    sa.Column('due_in_days', sa.Float(), nullable=False),
    sa.Column('due_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('measurement_id', sa.Uuid(), nullable=True),
    sa.Column('plan_version', sa.Integer(), nullable=False),
    sa.Column('reminder_count', sa.Integer(), nullable=False),
    sa.Column('last_reminded_at', app.db.types.UTCDateTime(), nullable=True),
    sa.Column('verify_result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['measurement_id'], ['measurements.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['pot_id'], ['pots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('checkpoints', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_checkpoints_due_at'), ['due_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_checkpoints_plan_id'), ['plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_checkpoints_pot_id'), ['pot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_checkpoints_status'), ['status'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('checkpoints', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_checkpoints_status'))
        batch_op.drop_index(batch_op.f('ix_checkpoints_pot_id'))
        batch_op.drop_index(batch_op.f('ix_checkpoints_plan_id'))
        batch_op.drop_index(batch_op.f('ix_checkpoints_due_at'))

    op.drop_table('checkpoints')
    with op.batch_alter_table('agent_questions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_questions_user_id'))
        batch_op.drop_index(batch_op.f('ix_agent_questions_run_id'))

    op.drop_table('agent_questions')
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_plans_state'))
        batch_op.drop_index(batch_op.f('ix_plans_pot_id'))

    op.drop_table('plans')
    with op.batch_alter_table('measurements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_measurements_pot_id'))
        batch_op.drop_index(batch_op.f('ix_measurements_measured_at'))
        batch_op.drop_index(batch_op.f('ix_measurements_device_id'))

    op.drop_table('measurements')
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sessions_device_id'))

    op.drop_table('sessions')
    with op.batch_alter_table('plantings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_plantings_pot_id'))

    op.drop_table('plantings')
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_messages_user_id'))

    op.drop_table('chat_messages')
    with op.batch_alter_table('pots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pots_owner_id'))

    op.drop_table('pots')
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_devices_owner_id'))
        batch_op.drop_index(batch_op.f('ix_devices_device_id'))

    op.drop_table('devices')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_firebase_uid'))

    op.drop_table('users')
    with op.batch_alter_table('agent_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_logs_user_id'))
        batch_op.drop_index(batch_op.f('ix_agent_logs_run_id'))
        batch_op.drop_index(batch_op.f('ix_agent_logs_pot_id'))
        batch_op.drop_index(batch_op.f('ix_agent_logs_plan_id'))

    op.drop_table('agent_logs')
