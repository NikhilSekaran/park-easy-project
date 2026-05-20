"""Initial schema with users, parking_spots, parking_sessions, and pricing_config tables."""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(120), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # Create parking_spots table
    op.create_table(
        'parking_spots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('spot_number', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('spot_number')
    )
    op.create_index(op.f('ix_parking_spots_spot_number'), 'parking_spots', ['spot_number'], unique=True)

    # Create parking_sessions table
    op.create_table(
        'parking_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('spot_id', sa.Integer(), nullable=False),
        sa.Column('vehicle_number', sa.String(50), nullable=False),
        sa.Column('entry_time', sa.DateTime(), nullable=False),
        sa.Column('exit_time', sa.DateTime()),
        sa.Column('fee', sa.Numeric(10, 2)),
        sa.Column('paid', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['spot_id'], ['parking_spots.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_parking_sessions_spot_id'), 'parking_sessions', ['spot_id'])
    op.create_index(op.f('ix_parking_sessions_user_id'), 'parking_sessions', ['user_id'])

    # Create pricing_config table
    op.create_table(
        'pricing_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('hourly_rate', sa.Numeric(10, 2), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('pricing_config')
    op.drop_index(op.f('ix_parking_sessions_user_id'), table_name='parking_sessions')
    op.drop_index(op.f('ix_parking_sessions_spot_id'), table_name='parking_sessions')
    op.drop_table('parking_sessions')
    op.drop_index(op.f('ix_parking_spots_spot_number'), table_name='parking_spots')
    op.drop_table('parking_spots')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
