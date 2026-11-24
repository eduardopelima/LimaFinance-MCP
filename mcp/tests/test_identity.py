"""Resolução de identidade (users.google_sub) contra SQLite real com as migrations."""

import asyncio

import pytest
from fakes import FakeD1

from limafinance.auth import IdentityConflict, upsert_user
from limafinance.db import DB


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db():
    return DB(FakeD1())


def test_new_google_user_is_created_with_sub(db):
    u = run(upsert_user(db, "Ana@Gmail.com", "Ana", "111"))
    assert u["email"] == "ana@gmail.com" and u["google_sub"] == "111" and u["id"].startswith("usr_")
    assert run(upsert_user(db, "ana@gmail.com", None, "111"))["id"] == u["id"]  # idempotente


def test_existing_email_user_gets_linked_to_sub(db):
    # conta criada antes por e-mail (import, PAT ou login antigo)
    legacy = run(upsert_user(db, "ana@gmail.com"))
    assert legacy["google_sub"] is None
    linked = run(upsert_user(db, "ana@gmail.com", None, "111"))
    assert linked["id"] == legacy["id"] and linked["google_sub"] == "111"


def test_email_change_on_google_keeps_same_user(db):
    u = run(upsert_user(db, "ana@gmail.com", None, "111"))
    moved = run(upsert_user(db, "ana.nova@gmail.com", None, "111"))
    assert moved["id"] == u["id"] and moved["email"] == "ana.nova@gmail.com"
    # o e-mail antigo não aponta mais para ninguém
    other = run(upsert_user(db, "ana@gmail.com", None, "222"))
    assert other["id"] != u["id"]


def test_recycled_email_does_not_inherit_data(db):
    run(upsert_user(db, "ana@gmail.com", None, "111"))
    with pytest.raises(IdentityConflict):
        run(upsert_user(db, "ana@gmail.com", None, "999"))  # mesma caixa de e-mail, OUTRA conta Google


def test_email_change_blocked_if_new_email_taken(db):
    a = run(upsert_user(db, "ana@gmail.com", None, "111"))
    run(upsert_user(db, "bia@gmail.com"))  # outro usuário (sem sub) já tem o e-mail
    still = run(upsert_user(db, "bia@gmail.com", None, "111"))
    assert still["id"] == a["id"] and still["email"] == "ana@gmail.com"


def test_without_sub_falls_back_to_email(db):
    u = run(upsert_user(db, "ana@gmail.com", None, "111"))
    assert run(upsert_user(db, "ana@gmail.com"))["id"] == u["id"]  # grant antigo / login dev
