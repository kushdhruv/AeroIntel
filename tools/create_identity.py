"""Print a local bearer credential once; store securely outside the repository."""

import argparse
import hashlib
import json
import secrets

parser = argparse.ArgumentParser()
parser.add_argument("role", choices=["reader", "operator", "approver"])
parser.add_argument("--id", default="local-user")
args = parser.parse_args()
token = secrets.token_urlsafe(32)
identity = {"id": args.id, "role": args.role, "human": args.role == "approver"}
print("Store this token securely; it is displayed once:")
print(token)
print("Merge this entry into the server's AEROINTEL_IDENTITIES JSON:")
print(json.dumps({hashlib.sha256(token.encode()).hexdigest(): identity}))
