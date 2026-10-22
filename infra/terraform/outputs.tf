output "public_ip" {
  description = "IP pública de la VM."
  value       = azurerm_public_ip.main.ip_address
}

output "api_url" {
  description = "URL de la API (Swagger UI en /docs). Tarda unos minutos en responder después del apply: cloud-init todavía está instalando Docker y construyendo las imágenes."
  value       = "http://${azurerm_public_ip.main.ip_address}:8000"
}

output "ssh_command" {
  description = "Para entrar a la VM. Ver el progreso del bootstrap con: cloud-init status --wait"
  value       = "ssh ${var.admin_username}@${azurerm_public_ip.main.ip_address}"
}
