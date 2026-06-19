# infra/terraform/variables.tf
#
# folder_id is read from TF_VAR_folder_id environment variable,
# which is sourced from the project's .env file (see bootstrap.sh).
# All other variables have sensible defaults for this project's scale.

variable "folder_id" {
  description = "Yandex Cloud folder ID (from .env TF_VAR_folder_id)"
  type        = string
}

variable "zone" {
  description = "Availability zone"
  type        = string
  default     = "ru-central1-a"
}

variable "vm_name" {
  description = "Name of the compute instance"
  type        = string
  default     = "moex-etf"
}

variable "platform_id" {
  description = "Compute platform (CPU generation)"
  type        = string
  default     = "standard-v3"
}

variable "cores" {
  description = "Number of vCPUs"
  type        = number
  default     = 2
}

variable "memory" {
  description = "RAM in GB"
  type        = number
  default     = 4
}

variable "disk_size" {
  description = "Boot disk size in GB"
  type        = number
  default     = 20
}

variable "image_family" {
  description = "OS image family"
  type        = string
  default     = "ubuntu-2204-lts"
}

variable "ssh_public_key_path" {
  description = "Path to local SSH public key, injected into the VM for access (set via TF_VAR_ssh_public_key_path in .env)"
  type        = string
}

# -----------------------------------------------------------------------
# First-deploy automation: cloud-init writes .env on the VM and runs
# `docker compose up` on first boot. These values must match the ones
# in your local .env (duplicated here with TF_VAR_ prefix), since
# Terraform and Docker Compose don't share a config source.
# -----------------------------------------------------------------------

variable "postgres_user" {
  description = "Postgres user for Airflow metadata + etf_db (from .env TF_VAR_postgres_user)"
  type        = string
  default     = "airflow"
}

variable "postgres_password" {
  description = "Postgres password (from .env TF_VAR_postgres_password)"
  type        = string
  sensitive   = true
}

variable "postgres_db" {
  description = "Postgres database name for Airflow metadata (from .env TF_VAR_postgres_db)"
  type        = string
  default     = "airflow_db"
}

variable "airflow_uid" {
  description = "UID for the Airflow container user (from .env TF_VAR_airflow_uid)"
  type        = string
  default     = "50000"
}

variable "repo_url" {
  description = "Git URL cloned onto the VM on first boot"
  type        = string
  default     = "https://github.com/stubchief/moex-fund-compare.git"
}
