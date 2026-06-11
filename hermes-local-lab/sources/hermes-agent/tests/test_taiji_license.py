import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import taiji_license


@pytest.fixture()
def signing_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


def _write_token(path, private_pem, **overrides):
    now = int(time.time())
    payload = {
        "license_id": "lic-test",
        "customer": "测试客户",
        "product": "taiji-agent",
        "aud": "taiji-agent",
        "iat": now - 60,
        "nbf": now - 60,
        "exp": now + 86400,
        "features": ["chat", "writing"],
    }
    payload.update(overrides)
    token = jwt.encode(payload, private_pem, algorithm="RS256")
    path.write_text(token, encoding="utf-8")
    return token


def test_valid_license_returns_public_status(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    _write_token(path, private_pem)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )

    assert status.status == "valid"
    assert status.code is None
    assert status.customer == "测试客户"
    assert status.product == "taiji-agent"
    assert status.remaining_days >= 0
    public = status.to_public_dict()
    assert public["status"] == "valid"
    assert public["customer"] == "测试客户"
    assert "token" not in public
    assert "path" not in public


def test_missing_required_license_has_stable_code(tmp_path, signing_keys):
    _, public_pem = signing_keys

    status = taiji_license.load_license_status(
        path=tmp_path / "missing.jwt",
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )

    assert status.status == "missing"
    assert status.code == "license_missing"
    assert "授权" in status.message


def test_expired_license_has_user_prompt(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "expired.jwt"
    now = int(time.time())
    _write_token(path, private_pem, exp=now - 10)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )

    assert status.status == "expired"
    assert status.code == "license_expired"
    assert status.message == "授权已到期，请联系服务方更新授权。"


def test_malformed_jwt_is_invalid(tmp_path, signing_keys):
    _, public_pem = signing_keys
    path = tmp_path / "broken.jwt"
    path.write_text("not-a-jwt", encoding="utf-8")

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )

    assert status.status == "invalid"
    assert status.code == "license_invalid"


def test_not_before_and_product_mismatch_are_invalid(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    now = int(time.time())

    future_path = tmp_path / "future.jwt"
    _write_token(future_path, private_pem, nbf=now + 3600)
    future = taiji_license.load_license_status(
        path=future_path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )
    assert future.status == "invalid"
    assert future.code == "license_not_yet_valid"

    product_path = tmp_path / "wrong-product.jwt"
    _write_token(product_path, private_pem, product="other-product", aud="other-product")
    mismatch = taiji_license.load_license_status(
        path=product_path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )
    assert mismatch.status == "invalid"
    assert mismatch.code in {"license_invalid_product", "license_invalid_audience"}
