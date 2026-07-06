"""Sprint 10+ P3-1 SSO 基础（认证框架 + RBAC）验证脚本

验证项：
1. AuthStore 用户 CRUD（create/get/list/update/delete）
2. 密码哈希与验证（salt + SHA256，时序安全比较）
3. Session 生命周期（create/verify/revoke/expire）
4. 角色层级继承（admin > operator > viewer）
5. /auth/login 端点（正确/错误凭证）
6. /auth/me 端点（session token 验证）
7. /auth/users CRUD（admin 守卫）
8. require_role 角色守卫（403 权限不足场景）
9. 向后兼容（开发模式放行 + legacy token）
10. bootstrap admin 引导
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_auth_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向 auth DB 到临时目录
import app.auth.models as auth_models

auth_models.DB_PATH = TMP_DIR / "auth.db"
# 清理单例
auth_models._store = None

PASS = 0
FAIL = 0


def check(cond: bool, msg: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {msg}")
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def test_user_crud() -> None:
    print("\n[1] AuthStore 用户 CRUD")
    from app.auth.models import get_auth_store

    store = get_auth_store()

    # create
    user = store.create_user("alice", "pass123", role="operator", display_name="Alice")
    check(user["username"] == "alice", f"创建用户 username=alice：{user['username']}")
    check(user["role"] == "operator", f"role=operator：{user['role']}")
    check("password_hash" not in user, "返回不含 password_hash")

    # get
    fetched = store.get_user("alice")
    check(fetched is not None and fetched["id"] == user["id"], "get_user 返回相同 id")

    # list
    users = store.list_users()
    check(len(users) == 1, f"list_users 返回 1 个：实际 {len(users)}")

    # update
    updated = store.update_user(user["id"], role="admin", email="alice@test.com")
    check(updated["role"] == "admin", f"update role=admin：{updated['role']}")
    check(updated["email"] == "alice@test.com", f"update email：{updated['email']}")

    # 重复创建
    try:
        store.create_user("alice", "pass", role="viewer")
        check(False, "重复用户名应报错")
    except ValueError:
        check(True, "重复用户名报 ValueError")

    # delete
    ok = store.delete_user(user["id"])
    check(ok is True, "delete_user 返回 True")
    check(store.get_user("alice") is None, "删除后 get_user 返回 None")


def test_password_hashing() -> None:
    print("\n[2] 密码哈希与验证")
    from app.auth.models import _hash_password, _verify_password, get_auth_store

    # 相同密码不同 salt
    h1 = _hash_password("secret")
    h2 = _hash_password("secret")
    check(h1 != h2, "相同密码生成不同哈希（随机 salt）")

    # 验证
    check(_verify_password("secret", h1) is True, "正确密码验证通过")
    check(_verify_password("wrong", h1) is False, "错误密码验证失败")

    # AuthStore.verify_password
    store = get_auth_store()
    store.create_user("bob", "mypass", role="viewer")
    check(store.verify_password("bob", "mypass") is not None, "正确凭证返回用户")
    check(store.verify_password("bob", "wrong") is None, "错误密码返回 None")
    check(store.verify_password("nobody", "pass") is None, "不存在用户返回 None")


def test_session_lifecycle() -> None:
    print("\n[3] Session 生命周期")
    from app.auth.models import get_auth_store

    store = get_auth_store()
    user = store.create_user("carol", "pass", role="admin")

    # create session
    token = store.create_session(user["id"], ttl_seconds=3600)
    check(isinstance(token, str) and len(token) > 20, f"create_session 返回 token：{token[:8]}...")

    # verify
    verified = store.verify_session(token)
    check(verified is not None and verified["username"] == "carol", "verify_session 返回用户")

    # revoke
    ok = store.revoke_session(token)
    check(ok is True, "revoke_session 返回 True")
    check(store.verify_session(token) is None, "revoke 后 verify 返回 None")

    # 过期 session
    expired_token = store.create_session(user["id"], ttl_seconds=0)
    import time
    time.sleep(0.1)
    check(store.verify_session(expired_token) is None, "过期 token verify 返回 None")

    # cleanup — 需创建新的过期 session（verify_session 会自动删除已过期的）
    store.create_session(user["id"], ttl_seconds=0)
    time.sleep(0.1)
    n = store.cleanup_expired_sessions()
    check(n >= 1, f"cleanup_expired_sessions 清理 {n} 个")


def test_role_hierarchy() -> None:
    print("\n[4] 角色层级继承")
    from app.auth.models import has_role

    check(has_role("admin", "admin") is True, "admin >= admin")
    check(has_role("admin", "operator") is True, "admin >= operator")
    check(has_role("admin", "viewer") is True, "admin >= viewer")
    check(has_role("operator", "admin") is False, "operator < admin")
    check(has_role("operator", "operator") is True, "operator >= operator")
    check(has_role("operator", "viewer") is True, "operator >= viewer")
    check(has_role("viewer", "admin") is False, "viewer < admin")
    check(has_role("viewer", "operator") is False, "viewer < operator")
    check(has_role("viewer", "viewer") is True, "viewer >= viewer")


def test_login_endpoint() -> None:
    print("\n[5] /auth/login 端点")
    from fastapi.testclient import TestClient

    # 清理并重建
    auth_models._store = None
    store = auth_models.get_auth_store()
    store.create_user("dave", "davepass", role="operator")

    from app.main import app

    with TestClient(app) as client:
        # 正确凭证
        r = client.post("/auth/login", json={"username": "dave", "password": "davepass"})
        check(r.status_code == 200, f"正确凭证返回 200：{r.status_code}")
        body = r.json()
        check("token" in body and len(body["token"]) > 20, "返回 token")
        check(body["user"]["username"] == "dave", "返回 user.username=dave")
        check(body["user"]["role"] == "operator", f"返回 user.role=operator：{body['user']['role']}")

        # 错误密码
        r2 = client.post("/auth/login", json={"username": "dave", "password": "wrong"})
        check(r2.status_code == 401, f"错误密码返回 401：{r2.status_code}")

        # 不存在用户
        r3 = client.post("/auth/login", json={"username": "nobody", "password": "pass"})
        check(r3.status_code == 401, f"不存在用户返回 401：{r3.status_code}")


def test_me_endpoint() -> None:
    print("\n[6] /auth/me 端点")
    from fastapi.testclient import TestClient

    store = auth_models.get_auth_store()
    user = store.create_user("eve", "evepass", role="viewer")
    token = store.create_session(user["id"])

    from app.main import app

    with TestClient(app) as client:
        # 带 session token
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        check(r.status_code == 200, f"带 token 返回 200：{r.status_code}")
        body = r.json()
        check(body["authenticated"] is True, "authenticated=True")
        check(body["user"]["username"] == "eve", "user.username=eve")

        # 不带 token（开发模式）
        r2 = client.get("/auth/me")
        check(r2.status_code == 200, f"无 token 返回 200（开发模式）：{r2.status_code}")
        check(r2.json()["authenticated"] is False, "开发模式 authenticated=False")


def test_users_admin_guard() -> None:
    print("\n[7] /auth/users admin 守卫")
    from fastapi.testclient import TestClient

    store = auth_models.get_auth_store()
    admin = store.create_user("admin1", "adminpass", role="admin")
    viewer = store.create_user("viewer1", "viewerpass", role="viewer")
    admin_token = store.create_session(admin["id"])
    viewer_token = store.create_session(viewer["id"])

    from app.main import app

    with TestClient(app) as client:
        # admin 访问
        r = client.get("/auth/users", headers={"Authorization": f"Bearer {admin_token}"})
        check(r.status_code == 200, f"admin 访问 /auth/users 200：{r.status_code}")

        # viewer 访问 → 403
        r2 = client.get("/auth/users", headers={"Authorization": f"Bearer {viewer_token}"})
        check(r2.status_code == 403, f"viewer 访问 /auth/users 403：{r2.status_code}")

        # admin 创建用户
        r3 = client.post(
            "/auth/users",
            json={"username": "newuser", "password": "pass", "role": "operator"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        check(r3.status_code == 200, f"admin 创建用户 200：{r3.status_code}")
        check(r3.json()["user"]["username"] == "newuser", "创建的 username=newuser")

        # viewer 创建用户 → 403
        r4 = client.post(
            "/auth/users",
            json={"username": "hack", "password": "pass", "role": "admin"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        check(r4.status_code == 403, f"viewer 创建用户 403：{r4.status_code}")


def test_require_role_guard() -> None:
    print("\n[8] require_role 角色守卫")
    from fastapi.testclient import TestClient

    store = auth_models.get_auth_store()
    op = store.create_user("op1", "oppass", role="operator")
    viewer = store.create_user("v1", "vpass", role="viewer")
    op_token = store.create_session(op["id"])
    viewer_token = store.create_session(viewer["id"])

    from app.main import app

    with TestClient(app) as client:
        # operator 可以访问 require_role("operator") 端点（/auth/users 需要 admin）
        # viewer 访问 admin 端点 → 403
        r = client.get("/auth/users", headers={"Authorization": f"Bearer {op_token}"})
        check(r.status_code == 403, f"operator 访问 admin 端点 403：{r.status_code}")

        r2 = client.get("/auth/users", headers={"Authorization": f"Bearer {viewer_token}"})
        check(r2.status_code == 403, f"viewer 访问 admin 端点 403：{r2.status_code}")

        # /auth/roles 无需认证
        r3 = client.get("/auth/roles")
        check(r3.status_code == 200, f"/auth/roles 无需认证 200：{r3.status_code}")
        check("admin" in r3.json()["roles"], "roles 含 admin")


def test_backward_compat() -> None:
    print("\n[9] 向后兼容（开发模式 + legacy token）")
    from fastapi.testclient import TestClient

    from app.main import app

    # 开发模式（无 OPSKG_API_TOKEN）
    with TestClient(app) as client:
        # 无 token 访问 /health（不应 401）
        r = client.get("/health")
        check(r.status_code == 200, f"开发模式 /health 200：{r.status_code}")

        # /auth/me 无 token → authenticated=False（不报 401）
        r2 = client.get("/auth/me")
        check(r2.status_code == 200, f"开发模式 /auth/me 200：{r2.status_code}")

    # legacy token 模式
    os.environ["API_TOKEN"] = "legacy-secret-token"
    # 清除 settings 缓存
    from app.config import get_settings
    get_settings.cache_clear()

    with TestClient(app) as client:
        # 无 token → 401（legacy 模式开启）
        r3 = client.get("/auth/me")
        # /auth/me 用 get_current_user 不抛 401，返回 authenticated=False
        check(r3.status_code == 200, f"legacy 模式 /auth/me 仍 200：{r3.status_code}")

        # legacy token 访问 admin 端点 → 200（视为 admin）
        r4 = client.get(
            "/auth/users",
            headers={"Authorization": "Bearer legacy-secret-token"},
        )
        check(r4.status_code == 200, f"legacy token 视为 admin 200：{r4.status_code}")

    # 恢复
    os.environ.pop("API_TOKEN", None)
    get_settings.cache_clear()


def test_bootstrap_admin() -> None:
    print("\n[10] bootstrap admin 引导")
    from app.auth.models import get_auth_store

    store = get_auth_store()
    # 首次调用应创建
    admin = store.ensure_bootstrap_admin("bootstrap_admin", "bootstrap_pass")
    check(admin["username"] == "bootstrap_admin", f"bootstrap 创建 admin：{admin['username']}")
    check(admin["role"] == "admin", f"bootstrap role=admin：{admin['role']}")

    # 第二次调用应返回已有（不重复创建）
    admin2 = store.ensure_bootstrap_admin("bootstrap_admin", "newpass")
    check(admin2["id"] == admin["id"], "bootstrap 幂等：返回相同 id")

    # 验证可登录
    verified = store.verify_password("bootstrap_admin", "bootstrap_pass")
    check(verified is not None, "bootstrap admin 可用初始密码登录")


def main() -> None:
    print("=" * 70)
    print("OpsKG Sprint 10+ P3-1 SSO 基础（认证框架 + RBAC）验证")
    print("=" * 70)

    test_user_crud()
    test_password_hashing()
    test_session_lifecycle()
    test_role_hierarchy()
    test_login_endpoint()
    test_me_endpoint()
    test_users_admin_guard()
    test_require_role_guard()
    test_backward_compat()
    test_bootstrap_admin()

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 70)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
