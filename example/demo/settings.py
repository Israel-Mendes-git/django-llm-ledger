import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "demo-only-not-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]
USE_TZ = True
TIME_ZONE = "America/Fortaleza"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "llm_ledger",
    "store",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "demo.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "demo.sqlite3"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

OLLAMA_MODEL = os.environ.get("DEMO_OLLAMA_MODEL", "qwen2.5:3b")

providers = []
if os.environ.get("ANTHROPIC_API_KEY"):
    providers.append({"name": "anthropic", "model": os.environ.get("DEMO_ANTHROPIC_MODEL", "claude-haiku-5-5")})
ollama = {"name": "ollama", "model": OLLAMA_MODEL}
if os.environ.get("DEMO_OLLAMA_CPU", "1") == "1":
    # On this machine the integrated GPU is far slower than the CPU for small models.
    ollama["model_options"] = {"num_gpu": 0}
providers.append(ollama)
# Last resort so the demo never shows a stack trace.
providers.append({"name": "fake", "model": "offline-notice",
                  "reply": "The assistant is offline right now. Please use 'Talk to a human' below."})

LLM_LEDGER = {
    "PROVIDERS": providers,
    # Placeholder prices for the demo. Check the provider's pricing page for real numbers.
    "PRICES": {
        "claude-haiku-5-5": {"input": 1.0, "output": 5.0},
        "claude-sonnet-5-5": {"input": 3.0, "output": 15.0},
        "gpt-4.1-mini": {"input": 0.4, "output": 1.6},
    },
    "MAX_RETRIES": 1,
    "RETRY_BACKOFF": 0.3,
    "TIMEOUT": 120,
    "MONTHLY_BUDGET_USD": 20,
    "LOCAL_CURRENCY": {"code": "BRL", "rate": 5.4},
    "STORE_PROMPTS": True,  # fine for a demo with fake customers
}
