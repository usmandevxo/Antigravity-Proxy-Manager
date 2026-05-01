import httpx
from kiro.core import get_accounts, get_access_token, _read_kiro_token_cache, decrypt_value

accounts = get_accounts()
if accounts:
    acc = accounts[0]
    token = get_access_token(acc['profile_arn'])
    
    # Let's try some endpoints on Kiro's backend
    urls_to_test = [
        "https://prod.us-east-1.auth.desktop.kiro.dev/profile",
        "https://prod.us-east-1.auth.desktop.kiro.dev/user",
        "https://prod.us-east-1.auth.desktop.kiro.dev/me",
        "https://app.kiro.dev/api/user",
        "https://app.kiro.dev/api/profile",
        "https://app.kiro.dev/api/credits",
        "https://app.kiro.dev/api/me"
    ]
    
    headers = {
        'User-Agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Kiro/0.1.36 Chrome/132.0.6834.210 Electron/34.5.2 Safari/537.36",
        'Authorization': f'Bearer {token}'
    }
    
    for url in urls_to_test:
        try:
            r = httpx.get(url, headers=headers)
            print(f"{url} -> {r.status_code}")
            if r.status_code == 200:
                print(r.text[:300])
        except Exception as e:
            print(f"Error for {url}: {e}")
