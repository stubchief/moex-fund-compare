# infra/terraform/terraform.tfvars
#
# Non-secret defaults, safe to commit.
# folder_id is intentionally NOT here - it's read from TF_VAR_folder_id
# in .env (see bootstrap.sh), so it never needs to be typed twice.

zone        = "ru-central1-a"
vm_name     = "moex-etf"
platform_id = "standard-v3"
cores       = 2
memory      = 4
disk_size   = 20
