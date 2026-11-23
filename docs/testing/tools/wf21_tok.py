r"""WF-21 token helper: mints a Clerk-shaped session JWT (both v1 org_* and v2 'o' claims) signed by the mock key (%TEMP%\wf21\mock_key.pem), and prints the public PEM. Mock only."""
import os, sys, time, secrets, jwt
from cryptography.hazmat.primitives import serialization
W = os.path.join(os.environ["TEMP"], "wf21")
PRIV = serialization.load_pem_private_key(open(os.path.join(W, "mock_key.pem"), "rb").read(), None)
def pub_pem(): return PRIV.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
def mint(sub, org=None, role="org:member", ttl=3600, azp="wf20-client", iss="http://127.0.0.1:8161"):
    now = int(time.time())
    c = {"iss": iss, "sub": sub, "azp": azp, "iat": now - 5, "nbf": now - 5, "exp": now + ttl, "jti": secrets.token_hex(6), "sid": "sess_" + secrets.token_hex(4), "email": sub + "@wf21.test"}
    if org:
        c.update({"org_id": org, "org_role": role, "org_slug": org.replace("_", "-"), "v": 2, "o": {"id": org, "rol": role.split(":")[-1], "slg": org.replace("_", "-")}})
    return jwt.encode(c, PRIV, algorithm="RS256", headers={"kid": "wf20-key"})
if __name__ == "__main__": print(pub_pem())
