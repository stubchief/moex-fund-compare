#!/bin/bash
# infra/bootstrap.sh
#
# Run once before the first Terraform apply. Creates a Yandex Cloud
# service account dedicated to Terraform, grants it editor rights on
# the target folder, and saves an authorized key for the provider.
#
# folder_id is read from .env (TF_VAR_folder_id) so it only needs
# to be entered in one place in the whole project.
#
# Requires: yc CLI authenticated with your personal account
#   https://cloud.yandex.ru/docs/cli/quickstart

set -e

ENV_FILE="$(dirname "$0")/../.env"
KEY_PATH="$(dirname "$0")/terraform/key.json"
SA_NAME="terraform-sa"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env not found at $ENV_FILE"
    echo "Copy .env.example to .env and fill in TF_VAR_folder_id first."
    exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

if [ -z "$TF_VAR_folder_id" ]; then
    echo "Error: TF_VAR_folder_id is not set in .env"
    echo "Run 'yc config list' to find your folder id."
    exit 1
fi

echo "Using folder_id: $TF_VAR_folder_id"

echo "Creating service account '$SA_NAME' (skipping if it already exists)..."
yc iam service-account create --name "$SA_NAME" --folder-id "$TF_VAR_folder_id" || true

echo "Granting 'editor' role on the folder..."
yc resource-manager folder add-access-binding \
    --id "$TF_VAR_folder_id" \
    --role editor \
    --service-account-name "$SA_NAME"

echo "Creating authorized key..."
yc iam key create \
    --service-account-name "$SA_NAME" \
    --output "$KEY_PATH"

echo ""
echo "Done. Key saved to $KEY_PATH (already in .gitignore)."
echo ""
echo "Next steps:"
echo "  terraform -chdir=infra/terraform init"
echo "  terraform -chdir=infra/terraform apply"