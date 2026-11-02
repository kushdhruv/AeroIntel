variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "project" {
  type    = string
  default = "aerointel"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.project))
    error_message = "Use 3-31 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "bucket_name" {
  type        = string
  description = "Globally unique bucket name owned by this demo."
}

variable "enable_training_pipeline" {
  type        = bool
  default     = false
  description = "Create the optional CPU Processing-based workflow definition; does not start jobs."
}

variable "worker_image_uri" {
  type        = string
  default     = ""
  description = "Approved ECR image built from the worker target, pinned to a digest."
  validation {
    condition     = var.worker_image_uri == "" || can(regex("@sha256:[0-9a-f]{64}$", var.worker_image_uri))
    error_message = "Pin a reviewed image digest."
  }
}

variable "log_retention_days" {
  type    = number
  default = 7
}
