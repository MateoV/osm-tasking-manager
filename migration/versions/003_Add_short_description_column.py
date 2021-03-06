from sqlalchemy import *
from migrate import *

def upgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine
    jobs = Table('jobs', meta,
      Column('id', Integer(),  primary_key=True, nullable=False),
      Column('title', String()),
      Column('description', String()),
      Column('geometry', String()),
      Column('workflow', String()),
      Column('imagery', String()),
      Column('zoom', Integer()),
      Column('is_private', Integer()),
      Column('requires_nextview', Integer()),
    )
    col = Column('short_description', String(), default='')
    col.create(jobs)
    pass

def downgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine
    jobs = Table('jobs', meta,
      Column('id', Integer(),  primary_key=True, nullable=False),
      Column('title', String()),
      Column('description', String()),
      Column('geometry', String()),
      Column('workflow', String()),
      Column('imagery', String()),
      Column('zoom', Integer()),
      Column('is_private', Integer()),
      Column('requires_nextview', Integer()),
      Column('short_description', String())
    )
    jobs.c.short_description.drop()
    pass
