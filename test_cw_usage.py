import httpx
from kiro.core import get_accounts, get_access_token
import json

accounts = get_accounts()
acc = accounts[0]
token = get_access_token(acc['profile_arn'])
headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

r = httpx.get(f"https://codewhisperer.us-east-1.amazonaws.com/getUsageLimits", headers=headers)
print(json.dumps(r.json(), indent=2))
