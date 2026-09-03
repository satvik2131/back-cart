from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Intentionally empty — no models are defined yet. Import model modules
    here (or ensure they are imported before Alembic autogenerate runs)
    once they exist so ``Base.metadata`` is aware of them.
    """
