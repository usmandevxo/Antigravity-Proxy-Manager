import httpx
from kiro.core import get_accounts, get_access_token

accounts = get_accounts()
acc = accounts[0]
token = get_access_token(acc['profile_arn'])
headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

payload = {"origin": "AI_EDITOR", "profileArn": acc['profile_arn'], "resourceType": "AGENTIC_REQUEST"}
for p in ['/usage-limits', '/getUsageLimits', '/get-usage-limits', '/usageLimits', '/GetUsageLimits', '/user/usageLimits']:
    r = httpx.post(f"https://codewhisperer.us-east-1.amazonaws.com{p}", headers=headers, json=payload)
    if r.status_code != 403 and r.status_code != 404:
        print(f"POST {p} -> {r.status_code} {r.text[:50]}")
    r2 = httpx.get(f"https://codewhisperer.us-east-1.amazonaws.com{p}", headers=headers)
    if r2.status_code != 403 and r2.status_code != 404:
        print(f"GET {p} -> {r2.status_code} {r2.text[:50]}")
