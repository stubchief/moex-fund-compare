terraform {
  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "~> 0.130"
    }
  }

  backend "s3" {
    endpoints = {
      s3 = "https://storage.yandexcloud.net"
    }
    bucket                      = "moex-fund-compare-tfstate"
    key                         = "terraform.tfstate"
    region                      = "ru-central1"

    skip_region_validation      = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "yandex" {
  service_account_key_file = "${path.module}/key.json"
  folder_id                = var.folder_id
  zone                     = var.zone
}

# -----------------------------------------------------------------------
# Network
# -----------------------------------------------------------------------

resource "yandex_vpc_network" "etf_network" {
  name = "${var.vm_name}-network"
}

resource "yandex_vpc_subnet" "etf_subnet" {
  name           = "${var.vm_name}-subnet"
  zone           = var.zone
  network_id     = yandex_vpc_network.etf_network.id
  v4_cidr_blocks = ["10.0.1.0/24"]
}

# -----------------------------------------------------------------------
# Security group
# -----------------------------------------------------------------------

resource "yandex_vpc_security_group" "etf_sg" {
  name       = "${var.vm_name}-sg"
  network_id = yandex_vpc_network.etf_network.id

  ingress {
    protocol       = "TCP"
    description    = "SSH"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    protocol       = "TCP"
    description    = "FastAPI dashboard"
    port           = 8000
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    protocol       = "TCP"
    description    = "Airflow UI"
    port           = 8080
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    protocol       = "ANY"
    description    = "Allow all outbound"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

# -----------------------------------------------------------------------
# Compute instance
# -----------------------------------------------------------------------

data "yandex_compute_image" "ubuntu" {
  family = var.image_family
}

resource "yandex_compute_instance" "etf_vm" {
  name        = var.vm_name
  platform_id = var.platform_id
  zone        = var.zone

  resources {
    cores  = var.cores
    memory = var.memory
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.disk_size
      type     = "network-hdd"
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.etf_subnet.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.etf_sg.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${file(var.ssh_public_key_path)}"
    user-data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
      postgres_user     = var.postgres_user
      postgres_password = var.postgres_password
      postgres_db       = var.postgres_db
      airflow_uid       = var.airflow_uid
      repo_url          = var.repo_url
    })
  }
}

# -----------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------

output "vm_external_ip" {
  description = "Public IP - use this for SSH and as the GitHub Actions deploy target"
  value       = yandex_compute_instance.etf_vm.network_interface[0].nat_ip_address
}

output "ssh_command" {
  value = "ssh ubuntu@${yandex_compute_instance.etf_vm.network_interface[0].nat_ip_address}"
}