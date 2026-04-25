from setuptools import setup, find_packages

setup(
    name="agpm",
    version="1.0.0",
    packages=find_packages(),
    py_modules=["cli", "core", "proxy", "web"],
    install_requires=[
        "rich",
        "httpx",
        "cryptography",
        "psutil",
        "Flask",
        "python-dotenv",
        "werkzeug",
    ],
    entry_points={
        "console_scripts": [
            "agpm = cli:main",
            "agpm-web = web:main",
            "agpm-proxy = proxy:main",
        ],
    },
    include_package_data=True,
    package_data={
        "": ["templates/*", "static/*", "data/.env.example", "README.md", "LICENSE"],
    },
    author="Usman",
    description="Antigravity Proxy Manager - OpenAI-compatible API proxy for Gemini",
)
