import pytest
from sqlalchemy import func, select

from app import models as m
from app.cli import load_school
from app.models import db
from app.solver_input import build_solver_input
from tools.make_demo_seeds import SCHOOLS
from tools.seed_to_contract import SEED, load_seed, seed_to_solver_input


@pytest.mark.parametrize("name", list(SCHOOLS))
def test_demo_seed_loads_into_the_db(migrated, name):
    seed = load_seed(SEED.parent / name)
    load_school(seed)
    db.session.commit()
    count = db.session.scalar(select(func.count()).select_from(m.Course))
    assert count == len(seed["curriculum"])
    from_db = build_solver_input(db.session)
    assert from_db.problems() == []
    assert len(from_db.courses) == len(seed_to_solver_input(seed).courses)
