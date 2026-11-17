variable "name" {
  description = "Name prefix, e.g. sr-dev."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets in two AZs."
  type        = list(string)
}

variable "client_security_group_ids" {
  description = "Security groups allowed to connect (the env's Lambda SG)."
  type        = list(string)
}

variable "major_engine_version" {
  description = "Valkey major version."
  type        = string
  default     = "8"
}
