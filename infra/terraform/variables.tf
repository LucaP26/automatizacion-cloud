variable "subscription_id" {
  description = "ID de la suscripción de Azure (az account show --query id -o tsv)."
  type        = string
}

variable "allowed_source_cidr" {
  description = "Rango de IPs que puede entrar por SSH y a la API, p.ej. \"203.0.113.7/32\" (tu IP pública). Nunca 0.0.0.0/0: la API va por HTTP sin TLS."
  type        = string

  validation {
    condition     = can(cidrhost(var.allowed_source_cidr, 0)) && var.allowed_source_cidr != "0.0.0.0/0"
    error_message = "Tiene que ser un CIDR válido y distinto de 0.0.0.0/0."
  }
}

variable "ssh_public_key_path" {
  description = "Path a la clave pública SSH para el usuario admin de la VM."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "location" {
  description = "Región de Azure. Azure for Students restringe regiones por policy (ver: az policy assignment list); si el apply falla con RequestDisallowedByAzure, probar otra de esa lista."
  type        = string
  default     = "chilecentral"
}

variable "project_name" {
  description = "Prefijo para nombrar los recursos."
  type        = string
  default     = "automation-manager"
}

variable "vm_size" {
  description = "Tamaño de la VM. B2ats_v2 (2 vCPU AMD, 1 GB RAM) es el reemplazo de B1s, que ya no se ofrece en las regiones de Azure for Students; ver disponibilidad con az vm list-skus --location <region>."
  type        = string
  default     = "Standard_B2ats_v2"
}

variable "admin_username" {
  description = "Usuario admin de la VM."
  type        = string
  default     = "azureuser"
}

variable "repo_url" {
  description = "Repo que la VM clona y levanta con docker compose."
  type        = string
  default     = "https://github.com/LucaP26/automatizacion-cloud.git"
}

variable "git_ref" {
  description = "Branch o tag a desplegar."
  type        = string
  default     = "main"
}

variable "notification_webhook_url" {
  description = "Opcional: webhook para notificar ejecuciones fallidas. Queda en el tfstate (sensitive solo lo oculta del output)."
  type        = string
  default     = ""
  sensitive   = true
}
