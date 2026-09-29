# tests/test_crud.py
import pytest
import psycopg
from xncagent.rdbms.postgres import get_cursor


# ============================================================
# CRUD 函数
# ============================================================

def create_user(username: str, email: str, age: int) -> int:
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, email, age) VALUES (%s, %s, %s) RETURNING id",
            (username, email, age),
        )
        return cur.fetchone()["id"]


def get_users() -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT id, username, email, age FROM users ORDER BY id")
        return cur.fetchall()


def get_user(user_id: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, username, email, age FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


def update_user(user_id: int, new_age: int) -> int:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET age = %s WHERE id = %s",
            (new_age, user_id),
        )
        return cur.rowcount


def delete_user(user_id: int) -> int:
    with get_cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        return cur.rowcount


# ============================================================
# 测试用例
# ============================================================

class TestCreate:
    def test_create_returns_id(self):
        uid = create_user("alice", "alice@example.com", 25)
        assert isinstance(uid, int)
        assert uid > 0

    def test_create_then_get(self):
        uid = create_user("bob", "bob@example.com", 30)
        user = get_user(uid)
        assert user["username"] == "bob"
        assert user["email"] == "bob@example.com"
        assert user["age"] == 30

    def test_duplicate_username_raises(self):
        create_user("eve", "eve@example.com", 22)
        with pytest.raises(psycopg.errors.UniqueViolation):
            create_user("eve", "eve2@example.com", 23)


class TestRead:
    def test_empty_table(self):
        assert get_users() == []

    def test_order_by_id(self):
        create_user("u1", "u1@example.com", 1)
        create_user("u2", "u2@example.com", 2)
        create_user("u3", "u3@example.com", 3)
        users = get_users()
        assert [u["username"] for u in users] == ["u1", "u2", "u3"]

    def test_get_nonexistent(self):
        assert get_user(99999) is None


class TestUpdate:
    def test_update_age(self):
        uid = create_user("carol", "carol@example.com", 20)
        affected = update_user(uid, 21)
        assert affected == 1
        assert get_user(uid)["age"] == 21

    def test_update_nonexistent(self):
        assert update_user(99999, 30) == 0


class TestDelete:
    def test_delete_existing(self):
        uid = create_user("dave", "dave@example.com", 40)
        assert delete_user(uid) == 1
        assert get_user(uid) is None

    def test_delete_nonexistent(self):
        assert delete_user(99999) == 0


class TestTransaction:
    def test_rollback_on_error(self):
        """插入两条，第二条违反唯一约束，第一条应该被回滚"""
        create_user("frank", "frank@example.com", 30)
        with pytest.raises(psycopg.errors.UniqueViolation):
            # 如果 CRUD 函数内部没有自己开事务，这里会延续到外层
            create_user("frank", "frank2@example.com", 31)
        # frank 还在，因为第一条是独立事务提交的
        assert get_user(1)["username"] == "frank"