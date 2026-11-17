variable "name" {
  description = "Name prefix, e.g. sr-dev."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "cidr" {
  description = "VPC CIDR (dev 10.10.0.0/16, prod 10.20.0.0/16)."
  type        = string
}

variable "azs" {
  description = "Exactly two AZs that offer the NAT instance type."
  type        = list(string)

  validation {
    condition     = length(var.azs) == 2
    error_message = "Two AZs: ElastiCache Serverless needs subnets in at least two."
  }
}

variable "nat_instance_type" {
  description = "NAT instance type. The CI boundary and conftest allow only t4g.micro."
  type        = string
  default     = "t4g.micro"
}

variable "flow_log_retention_days" {
  description = "VPC flow log retention."
  type        = number
  default     = 3
}

variable "permissions_boundary_arn" {
  description = "sr-workload-boundary ARN."
  type        = string
}
