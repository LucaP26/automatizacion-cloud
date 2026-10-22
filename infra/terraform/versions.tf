terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.10"
    }
  }

  # State local, a propósito (ver README, Fase 5): un backend remoto en un
  # Storage Account necesitaría crear ese Storage Account ANTES y por fuera
  # de este Terraform, y `terraform destroy` ya no se llevaría todo.
}

provider "azurerm" {
  features {
    resource_group {
      # El destroy borra el resource group aunque Azure haya creado
      # recursos adentro por su cuenta (p.ej. NetworkWatcher), para que no
      # quede nada cobrando después de un destroy.
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id = var.subscription_id
}
