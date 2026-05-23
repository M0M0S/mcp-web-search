#!/bin/bash
# Auto-generates .env from .env.example on first run

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../.."

ENV_FILE="${PROJECT_ROOT}/.env"
EXAMPLE_ENV_FILE="${PROJECT_ROOT}/.env.example"

# If .env already exists, skip generation
if [ -f "$ENV_FILE" ]; then
    echo ".env file already exists. Skipping generation."
    exit 0
fi

# If .env.example is missing, exit with error
if [ ! -f "$EXAMPLE_ENV_FILE" ]; then
    echo "ERROR: .env.example file not found!"
    exit 1
fi

# Copy .env.example to .env
cp "$EXAMPLE_ENV_FILE" "$ENV_FILE"

# In Docker containers, REDIS_HOST should be 'redis' (service name)
if grep -q "^REDIS_HOST=" "$ENV_FILE"; then
    sed -i.bak "s|^REDIS_HOST=.*|REDIS_HOST=redis|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
fi

# Generate a strong API_KEY if it is empty or contains the default value
API_KEY_LINE=$(grep "^API_KEY=" "$ENV_FILE")
CURRENT_VALUE="${API_KEY_LINE#*=}"
# Remove comments and spaces from the value
CLEAN_VALUE=$(echo "$CURRENT_VALUE" | sed 's/#.*//' | tr -d ' ')

if [ -z "$CLEAN_VALUE" ] || [ "$CLEAN_VALUE" = "local_dev_token" ]; then
    # Generate a strong token: 32 characters (letters, digits, special chars)
    NEW_API_KEY=$(openssl rand -base64 32 | tr -d '\n' | cut -c1-32)
    sed -i.bak "s|^API_KEY=.*|API_KEY=${NEW_API_KEY}|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"

    echo "API_KEY successfully generated"
fi

echo ".env file successfully created from .env.example"
echo "Location: $ENV_FILE"
echo ""
echo "Please review and adjust settings in .env as needed"

