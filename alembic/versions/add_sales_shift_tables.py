"""
FILE: alembic/versions/add_sales_shift_tables.py

Run: alembic revision --autogenerate -m "add_sales_shift_tables"
OR manually create this migration file.

This creates: shifts, shift_personnel, shift_points, sale_logs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'add_sales_shift_tables'
down_revision = None  # SET THIS to your latest migration hash
branch_labels = None
depends_on = None


def upgrade():
    # ── shifts ──────────────────────────────────────────────────
    op.create_table(
        'shifts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pump_id', sa.Integer(), sa.ForeignKey('pumps.id'), nullable=False),
        sa.Column('shift_type', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_shifts_pump_id', 'shifts', ['pump_id'])
    op.create_index('ix_shifts_status', 'shifts', ['status'])

    # ── shift_personnel ─────────────────────────────────────────
    op.create_table(
        'shift_personnel',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shift_id', sa.Integer(), sa.ForeignKey('shifts.id'), nullable=False),
        sa.Column('attendant_id', sa.Integer(), sa.ForeignKey('attendants.id'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_shift_personnel_shift_id', 'shift_personnel', ['shift_id'])

    # ── shift_points ────────────────────────────────────────────
    op.create_table(
        'shift_points',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shift_id', sa.Integer(), sa.ForeignKey('shifts.id'), nullable=False),
        sa.Column('nozzle_id', sa.Integer(), nullable=False),
        sa.Column('item_name', sa.String(100), nullable=False),
        sa.Column('start_reading', sa.Float(), nullable=False),
        sa.Column('end_reading', sa.Float(), nullable=True),
        sa.Column('testing_value', sa.Float(), server_default='0'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_shift_points_shift_id', 'shift_points', ['shift_id'])

    # ── sale_logs ───────────────────────────────────────────────
    op.create_table(
        'sale_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shift_id', sa.Integer(), sa.ForeignKey('shifts.id'), nullable=True),
        sa.Column('pump_id', sa.Integer(), sa.ForeignKey('pumps.id'), nullable=False),
        sa.Column('sale_type', sa.String(20), nullable=False, server_default='single'),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('nozzle_id', sa.Integer(), nullable=True),
        sa.Column('item_name', sa.String(100), nullable=False),
        sa.Column('rate', sa.Float(), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('payment_mode', sa.String(20), nullable=False),
        sa.Column('pos_machine', sa.String(100), nullable=True),
        sa.Column('billing_ref', sa.String(200), nullable=True),
        sa.Column('customer_name', sa.String(200), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('credit_slip_ref', sa.String(200), nullable=True),
        sa.Column('vehicle_number', sa.String(50), nullable=True),
        sa.Column('vehicle_type', sa.String(50), nullable=True),
        sa.Column('attendant_id', sa.Integer(), sa.ForeignKey('attendants.id'), nullable=True),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('receipt_url', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('is_deleted', sa.Boolean(), server_default='false'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sale_logs_pump_id', 'sale_logs', ['pump_id'])
    op.create_index('ix_sale_logs_shift_id', 'sale_logs', ['shift_id'])
    op.create_index('ix_sale_logs_payment_mode_ts', 'sale_logs', ['payment_mode', 'timestamp'])
    op.create_index('ix_sale_logs_customer_id', 'sale_logs', ['customer_id'])


def downgrade():
    op.drop_table('sale_logs')
    op.drop_table('shift_points')
    op.drop_table('shift_personnel')
    op.drop_table('shifts')