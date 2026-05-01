import httpx
from kiro.core import get_accounts, get_access_token
import json

accounts = get_accounts()
if accounts:
    acc = accounts[0]
    token = get_access_token(acc['profile_arn'])
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'aws-sdk-js/3.0.0'
    }
    
    url = "https://codewhisperer.us-east-1.amazonaws.com/GetUsageLimits"
    payload = {
        "origin": "AI_EDITOR",
        "profileArn": acc['profile_arn'],
        "resourceType": "AGENTIC_REQUEST"
    }
    
    try:
        r = httpx.post(url, headers=headers, json=payload)
        print(f"POST {url} -> {r.status_code}")
        if r.status_code == 200:
            print(json.dumps(r.json(), indent=2))
        else:
            print(r.text)
    except Exception as e:
        print(e)
