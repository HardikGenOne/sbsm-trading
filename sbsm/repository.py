"""Explicit domain queries shared by the SQL and Mongo storage adapters.

Only named fields and the operators below are supported. Raw user queries never
reach this interface; API filters are constructed from validated values.
"""
from sqlalchemy import and_, func, or_, select


def sql_filter(model, query):
    clauses = []
    for field, value in query.items():
        if field == "$or":
            clauses.append(or_(*(sql_filter(model, item) for item in value)))
            continue
        column = model.__table__.columns[field]
        if not isinstance(value, dict):
            clauses.append(column == value)
            continue
        for operator, operand in value.items():
            if operator == "$in":
                clauses.append(column.in_(operand))
            elif operator == "$ne":
                clauses.append(column != operand)
            elif operator == "$gt":
                clauses.append(column > operand)
            elif operator == "$gte":
                clauses.append(column >= operand)
            elif operator == "$lte":
                clauses.append(column <= operand)
            else:
                raise ValueError("Unsupported repository operator")
    return and_(*clauses) if clauses else True


def find(session, model, query=None, *, sort=None, limit=None, offset=0):
    query = query or {}
    if hasattr(session, "find_records"):
        return session.find_records(model, query, sort=sort, limit=limit, offset=offset)
    statement = select(model).where(sql_filter(model, query))
    for field, direction in sort or []:
        column = model.__table__.columns[field]
        statement = statement.order_by(column.asc() if direction == 1 else column.desc())
    if offset:
        statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return session.scalars(statement).all()


def one(session, model, query=None, *, sort=None):
    values = find(session, model, query, sort=sort, limit=1)
    return values[0] if values else None


def count(session, model, query=None):
    if hasattr(session, "count_records"):
        return session.count_records(model, query or {})
    return session.scalar(select(func.count()).select_from(model).where(sql_filter(model, query or {})))


def total(session, model, field):
    if hasattr(session, "sum_records"):
        return session.sum_records(model, field)
    return session.scalar(select(func.coalesce(func.sum(model.__table__.columns[field]), 0)))


def grouped_counts(session, model, field):
    if hasattr(session, "grouped_counts"):
        return session.grouped_counts(model, field)
    column = model.__table__.columns[field]
    return dict(session.execute(select(column, func.count()).group_by(column)).all())
