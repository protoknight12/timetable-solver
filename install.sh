#!/usr/bin/env bash
# SATurn installer for a school's own Linux server (Ubuntu/Debian tested in mind).
# Safe to re-run: an existing .env and the database volume are never touched.
set -euo pipefail
cd "$(dirname "$0")"

die() { echo "Грешка: $*" >&2; exit 1; }

# --- 1. Docker + Compose v2 (never installed automatically) ---------------------------
command -v docker >/dev/null 2>&1 ||
  die "Docker не е инсталиран. Инсталирайте Docker Engine: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 ||
  die "Липсва Docker Compose v2 (командата 'docker compose'). Вижте https://docs.docker.com/compose/install/linux/"
docker info >/dev/null 2>&1 ||
  die "Docker не отговаря. Стартирайте услугата или добавете потребителя към групата 'docker' (или пуснете със sudo)."

# --- 2. .env ---------------------------------------------------------------------------
# set_env KEY VALUE: replace the KEY=... line in .env (or append it). The value goes
# through the environment, so it never appears in a process list.
set_env() {
  local tmp
  tmp=$(mktemp .env.XXXXXX)
  VAL=$2 awk -v k="$1" 'BEGIN { FS = OFS = "=" }
    $1 == k { print k "=" ENVIRON["VAL"]; found = 1; next }
    { print }
    END { if (!found) print k "=" ENVIRON["VAL"] }' .env >"$tmp"
  mv "$tmp" .env
}
# 32 random bytes as hex from the kernel: needs no openssl or python.
random_hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }

if [ -f .env ]; then
  echo ".env вече съществува: оставям го непроменен."
  for key in SECRET_KEY POSTGRES_PASSWORD; do
    grep -q "^$key=." .env ||
      echo "Внимание: $key е празен в .env. Задайте му стойност (за POSTGRES_PASSWORD: само ако базата още не е създадена)." >&2
  done
else
  [ -f .env.example ] || die "Липсва .env.example."
  cp .env.example .env
  chmod 600 .env
  set_env SECRET_KEY "$(random_hex)"
  set_env POSTGRES_PASSWORD "$(random_hex)"
  echo "Въведете ключ за Anthropic API (по желание, Enter = без изкуствен интелект; текстът не се показва)."
  read -r -s -p "Ключ: " api_key
  echo
  if [ -n "$api_key" ]; then
    set_env ANTHROPIC_API_KEY "$api_key"
    set_env LLM_ENABLED true
  else
    set_env LLM_ENABLED false
  fi
  unset api_key
  echo "Създаден е .env със случайни SECRET_KEY и парола за базата."
fi

# --- 3. Start the stack, run migrations -------------------------------------------------
docker compose up -d --build
docker compose run --rm -T web flask --app app db upgrade

# --- 4. First administrator -------------------------------------------------------------
admin_exists() {
  local out
  out=$(docker compose run --rm -T web python -c "
from sqlalchemy import select
from app import create_app
from app.models import User, db
with create_app().app_context():
    print('yes' if db.session.scalar(select(User.id).where(User.role == 'admin').limit(1)) else 'no')
" 2>&1) || die "Неуспешна проверка за администратор: $out"
  [ "${out##*$'\n'}" = yes ]
}

if admin_exists; then
  echo "Вече има администратор: пропускам създаването."
else
  echo "Създаване на първия администратор."
  while :; do
    read -r -p "Потребителско име: " admin_name
    case $admin_name in
      "" | *[[:space:]]*) echo "Въведете име без интервали." >&2 ;;
      *) [ "${#admin_name}" -le 64 ] && break; echo "Най-много 64 знака." >&2 ;;
    esac
  done
  while :; do
    read -r -s -p "Парола (поне 8 знака): " SATURN_ADMIN_PASSWORD
    echo
    read -r -s -p "Повторете паролата: " confirm
    echo
    # 8 = MIN_PASSWORD_LENGTH in app/auth.py; the CLI enforces it too.
    if [ "$SATURN_ADMIN_PASSWORD" != "$confirm" ]; then
      echo "Паролите не съвпадат." >&2
    elif [ "${#SATURN_ADMIN_PASSWORD}" -lt 8 ]; then
      echo "Паролата е твърде кратка." >&2
    else
      break
    fi
  done
  unset confirm
  export SATURN_ADMIN_PASSWORD
  # -e VAR (no value) forwards the variable from this shell: the password is not on any command line.
  docker compose run --rm -T -e SATURN_ADMIN_PASSWORD web \
    flask --app app create-user "$admin_name" --role admin --password-env SATURN_ADMIN_PASSWORD
  unset SATURN_ADMIN_PASSWORD
fi

# --- 5. Done ----------------------------------------------------------------------------
host=$(hostname -I 2>/dev/null | awk '{print $1}') || true
echo
echo "Готово. SATurn работи на http://${host:-localhost}:8000"
