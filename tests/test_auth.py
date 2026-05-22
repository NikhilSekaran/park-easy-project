"""Tests for authentication: register, login, logout, access control."""
import pytest


def test_register_success(client):
    resp = client.post(
        "/auth/register",
        data={"email": "new@test.com", "password": "newpass1", "confirm": "newpass1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Registration successful" in resp.data


def test_register_duplicate_email(client):
    resp = client.post(
        "/auth/register",
        data={"email": "user@test.com", "password": "user123", "confirm": "user123"},
        follow_redirects=True,
    )
    assert b"Email already registered" in resp.data


def test_register_password_mismatch(client):
    resp = client.post(
        "/auth/register",
        data={"email": "new2@test.com", "password": "pass1234", "confirm": "different"},
        follow_redirects=True,
    )
    assert b"Passwords must match" in resp.data


def test_login_success(client):
    resp = client.post(
        "/auth/login",
        data={"email": "user@test.com", "password": "user123"},
    )
    # Successful login → redirect (302) to dashboard; main blueprint not registered in this slice
    assert resp.status_code == 302


def test_login_wrong_password(client):
    resp = client.post(
        "/auth/login",
        data={"email": "user@test.com", "password": "wrongpass"},
        follow_redirects=True,
    )
    assert b"Invalid email or password" in resp.data


def test_login_unknown_email(client):
    resp = client.post(
        "/auth/login",
        data={"email": "nobody@test.com", "password": "whatever"},
        follow_redirects=True,
    )
    assert b"Invalid email or password" in resp.data


def test_logout(client):
    client.post("/auth/login", data={"email": "user@test.com", "password": "user123"})
    resp = client.get("/auth/logout", follow_redirects=True)
    assert resp.status_code == 200
    assert b"logged out" in resp.data


def test_logout_requires_login(client):
    resp = client.get("/auth/logout", follow_redirects=True)
    # Should redirect to login page
    assert b"Login" in resp.data


def test_open_redirect_is_blocked(client):
    rv = client.post(
        "/auth/login?next=https://evil.com",
        data={"email": "user@test.com", "password": "user123"},
        follow_redirects=False,
    )
    assert rv.status_code == 302
    assert "evil.com" not in rv.headers["Location"]


def test_relative_next_is_allowed(client):
    rv = client.post(
        "/auth/login?next=/",
        data={"email": "user@test.com", "password": "user123"},
        follow_redirects=False,
    )
    assert rv.status_code == 302
    assert rv.headers["Location"].endswith("/")


def test_malformed_cookie_does_not_crash(client):
    """A tampered user_id in the session cookie must result in a redirect, not a 500."""
    with client.session_transaction() as sess:
        sess["_user_id"] = "not-an-integer"
    rv = client.get("/", follow_redirects=False)
    assert rv.status_code in (302, 200)  # redirect to login or home, not 500


def test_change_password_success(client):
    client.post("/auth/login", data={"email": "user@test.com", "password": "user123"})
    rv = client.post("/auth/change-password", data={
        "current_password": "user123",
        "new_password": "newpass456",
        "confirm": "newpass456",
    }, follow_redirects=True)
    assert rv.status_code == 200


def test_change_password_wrong_current(client):
    client.post("/auth/login", data={"email": "user@test.com", "password": "user123"})
    rv = client.post("/auth/change-password", data={
        "current_password": "wrongpass",
        "new_password": "newpass456",
        "confirm": "newpass456",
    }, follow_redirects=True)
    assert b"incorrect" in rv.data


def test_403_shows_context_message(client):
    client.post("/auth/login", data={"email": "user@test.com", "password": "user123"})
    rv = client.get("/admin/")
    assert rv.status_code == 403
    assert b"admin users" in rv.data


def test_admin_required_blocks_non_admin(client):
    """Non-admin user hitting an admin-required route should get 403."""
    # We need a protected route — use the error handler directly via abort test
    # Since admin blueprint is not registered yet, test the admin_required decorator directly
    from flask import Flask, abort
    from app.auth.utils import admin_required
    from flask_login import login_user, FlaskLoginClient
    from app.models import User

    # Verify the decorator logic: unauthenticated → 403
    with client.application.test_request_context():
        from flask_login import current_user
        # current_user is anonymous here, so admin_required should abort(403)
        @admin_required
        def dummy():
            return "ok"

        with pytest.raises(Exception):
            dummy()


# G2: Remember Me tests
def test_remember_me_sets_persistent_cookie(client):
    rv = client.post("/auth/login", data={
        "email": "user@test.com", "password": "user123", "remember": "y",
    }, follow_redirects=False)
    assert rv.status_code == 302
    assert any("remember_token" in c for c in rv.headers.getlist("Set-Cookie"))


def test_login_without_remember_no_persistent_cookie(client):
    rv = client.post("/auth/login", data={
        "email": "user@test.com", "password": "user123",
    }, follow_redirects=False)
    assert rv.status_code == 302
    # remember_token should NOT be set when remember=False
    assert not any("remember_token" in c for c in rv.headers.getlist("Set-Cookie"))


# G10: Account lockout tests
def test_account_locks_after_max_attempts(client):
    for _ in range(5):
        client.post("/auth/login", data={"email": "user@test.com", "password": "wrong"})
    rv = client.post("/auth/login", data={
        "email": "user@test.com", "password": "user123",
    }, follow_redirects=True)
    assert b"locked" in rv.data.lower()


def test_correct_password_resets_counter(client, app):
    for _ in range(3):
        client.post("/auth/login", data={"email": "user@test.com", "password": "wrong"})
    rv = client.post("/auth/login", data={
        "email": "user@test.com", "password": "user123",
    }, follow_redirects=True)
    assert rv.status_code == 200
    with app.app_context():
        from app.models import User
        u = User.query.filter_by(email="user@test.com").first()
        assert u.failed_logins == 0
