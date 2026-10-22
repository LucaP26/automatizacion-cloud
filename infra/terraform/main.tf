locals {
  tags = {
    project    = var.project_name
    managed_by = "terraform"
  }
}

# Todo vive en un único resource group: `terraform destroy` (o, en el peor
# caso, borrar el RG a mano desde el portal) se lleva todo de una.
resource "azurerm_resource_group" "main" {
  name     = "rg-${var.project_name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "main" {
  name                = "vnet-${var.project_name}"
  address_space       = ["10.0.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_subnet" "main" {
  name                 = "snet-app"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_public_ip" "main" {
  name                = "pip-${var.project_name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

# Solo dos puertos abiertos, y solo para `allowed_source_cidr`. Postgres no
# aparece acá: ni siquiera se publica en la VM (ver docker-compose.yml).
resource "azurerm_network_security_group" "main" {
  name                = "nsg-${var.project_name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  security_rule {
    name                       = "allow-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.allowed_source_cidr
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "allow-api"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8000"
    source_address_prefix      = var.allowed_source_cidr
    destination_address_prefix = "*"
  }
}

resource "azurerm_network_interface" "main" {
  name                = "nic-${var.project_name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.main.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.main.id
  }
}

resource "azurerm_network_interface_security_group_association" "main" {
  network_interface_id      = azurerm_network_interface.main.id
  network_security_group_id = azurerm_network_security_group.main.id
}

resource "azurerm_linux_virtual_machine" "main" {
  name                  = "vm-${var.project_name}"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.vm_size
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.main.id]
  tags                  = local.tags

  disable_password_authentication = true

  admin_ssh_key {
    username   = var.admin_username
    public_key = file(pathexpand(var.ssh_public_key_path))
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS" # P6 (hasta 64 GB): incluido en el free tier
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  # cloud-init: instala Docker, clona el repo, genera los secretos ADENTRO
  # de la VM y levanta docker compose. Ningún secreto de la app pasa por
  # Terraform (salvo el webhook opcional), así que no quedan en el tfstate.
  custom_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
    repo_url                 = var.repo_url
    git_ref                  = var.git_ref
    notification_webhook_url = var.notification_webhook_url
  }))
}
