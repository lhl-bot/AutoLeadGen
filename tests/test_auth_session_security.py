import asyncio

import httpx

import models
from fastapi import Response
from services.auth import AUTH_CSRF_COOKIE, AUTH_SESSION_COOKIE, hash_password
from services.auth import set_auth_cookies


def test_production_session_cookie_is_secure(monkeypatch):
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    response = Response()
    set_auth_cookies(response, "signed-session")

    set_cookie = ", ".join(response.headers.getlist("set-cookie")).lower()
    assert "secure" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie


def test_browser_session_is_httponly_and_requires_csrf_for_writes(db_session):
    user = models.User(
        username="session-owner",
        hashed_password=hash_password("correct horse battery staple"),
        is_active=True,
        is_admin=True,
    )
    db_session.add(user)
    db_session.commit()

    import main

    async def exercise_session():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "session-owner", "password": "correct horse battery staple"},
            )
            assert login.status_code == 200
            set_cookie = ", ".join(login.headers.get_list("set-cookie")).lower()
            assert f"{AUTH_SESSION_COOKIE}=" in set_cookie
            assert "httponly" in set_cookie
            assert "samesite=strict" in set_cookie
            assert f"{AUTH_CSRF_COOKIE}=" in set_cookie

            me = await client.get("/api/auth/me")
            assert me.status_code == 200
            assert me.json()["username"] == "session-owner"

            rejected = await client.post("/api/auth/logout")
            assert rejected.status_code == 403
            assert rejected.json()["detail"]["code"] == "CSRF_CHECK_FAILED"

            csrf = client.cookies.get(AUTH_CSRF_COOKIE)
            accepted = await client.post(
                "/api/auth/logout",
                headers={"X-CSRF-Token": csrf},
            )
            assert accepted.status_code == 204
            assert AUTH_SESSION_COOKIE not in client.cookies
            assert AUTH_CSRF_COOKIE not in client.cookies

    asyncio.run(exercise_session())


def test_bearer_clients_remain_compatible_without_csrf(db_session):
    user = models.User(
        username="api-owner",
        hashed_password=hash_password("correct horse battery staple"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    import main

    async def exercise_bearer():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "api-owner", "password": "correct horse battery staple"},
            )
            token = login.json()["token"]
            client.cookies.clear()
            logout = await client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert logout.status_code == 204

    asyncio.run(exercise_bearer())


def test_successful_logins_do_not_exhaust_failure_rate_limit(db_session):
    user = models.User(
        username="repeat-owner",
        hashed_password=hash_password("correct horse battery staple"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    import main

    async def repeat_success():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://repeat-success") as client:
            statuses = []
            for _ in range(8):
                response = await client.post(
                    "/api/auth/login",
                    json={
                        "username": "repeat-owner",
                        "password": "correct horse battery staple",
                    },
                )
                statuses.append(response.status_code)
            assert statuses == [200] * 8

    asyncio.run(repeat_success())


def test_failed_logins_are_rate_limited_per_identity(db_session):
    user = models.User(
        username="limited-owner",
        hashed_password=hash_password("correct horse battery staple"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    import main

    async def repeat_failure():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://repeat-failure") as client:
            statuses = []
            for _ in range(6):
                response = await client.post(
                    "/api/auth/login",
                    json={"username": "limited-owner", "password": "wrong-password"},
                )
                statuses.append(response.status_code)
            assert statuses == [401, 401, 401, 401, 401, 429]

    asyncio.run(repeat_failure())
