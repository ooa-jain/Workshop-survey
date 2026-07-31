import hashlib
import secrets

SALT_BYTES = 16
ITERATIONS = 100000


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Hashes a plain text password using PBKDF2-HMAC-SHA256.
    Returns (hex_hash, salt_hex). If salt is not provided, a new random salt is generated."""
    if not salt:
        salt = secrets.token_hex(SALT_BYTES)
    
    pwd_bytes = password.encode('utf-8')
    salt_bytes = bytes.fromhex(salt)
    hash_bytes = hashlib.pbkdf2_hmac('sha256', pwd_bytes, salt_bytes, ITERATIONS)
    return hash_bytes.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verifies a candidate password against stored hash and salt."""
    if not stored_hash or not salt or not password:
        return False
    candidate_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate_hash, stored_hash)
