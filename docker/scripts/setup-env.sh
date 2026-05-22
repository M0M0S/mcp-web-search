#!/bin/bash
# Скрипт автогенерации .env из .env.example при первом запуске

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../.."

ENV_FILE="${PROJECT_ROOT}/.env"
EXAMPLE_ENV_FILE="${PROJECT_ROOT}/.env.example"

# Если .env уже существует, ничего не делаем
if [ -f "$ENV_FILE" ]; then
    echo ".env файл уже существует. Пропускаем генерацию."
    exit 0
fi

# Если .env.example отсутствует, выводим ошибку
if [ ! -f "$EXAMPLE_ENV_FILE" ]; then
    echo "ОШИБКА: Файл .env.example не найден!"
    exit 1
fi

# Копируем .env.example в .env
cp "$EXAMPLE_ENV_FILE" "$ENV_FILE"

# В Docker контейнерах REDIS_HOST должен быть 'redis' (имя сервиса)
if grep -q "^REDIS_HOST=" "$ENV_FILE"; then
    sed -i.bak "s|^REDIS_HOST=.*|REDIS_HOST=redis|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
fi

# Генерация сложного токена API_KEY если он пустой или содержит значение по умолчанию
API_KEY_LINE=$(grep "^API_KEY=" "$ENV_FILE")
CURRENT_VALUE="${API_KEY_LINE#*=}"
# Удаляем комментарии и пробелы из значения
CLEAN_VALUE=$(echo "$CURRENT_VALUE" | sed 's/#.*//' | tr -d ' ')

if [ -z "$CLEAN_VALUE" ] || [ "$CLEAN_VALUE" = "local_dev_token" ]; then
    # Генерируем сложный токен: 32 символа (буквы, цифры, спецсимволы)
    NEW_API_KEY=$(openssl rand -base64 32 | tr -d '\n' | cut -c1-32)
    sed -i.bak "s|^API_KEY=.*|API_KEY=${NEW_API_KEY}|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
    
    echo "API_KEY успешно сгенерирован: ${NEW_API_KEY}"
fi

echo ".env файл успешно создан из .env.example"
echo "Расположение: $ENV_FILE"
echo ""
echo "Пожалуйста, проверьте и при необходимости измените настройки в .env"

