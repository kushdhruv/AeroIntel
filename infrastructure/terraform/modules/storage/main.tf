variable "bucket_name" {
  type = string
}

resource "aws_s3_bucket" "platform" {
  #checkov:skip=CKV_AWS_145:SSE-S3 protects demo objects; recurring customer KMS key charges are deliberately avoided.
  #checkov:skip=CKV_AWS_18:Dedicated access-log bucket is deferred for a short-lived demo; enable before production.
  #checkov:skip=CKV_AWS_144:Single-region demo intentionally avoids replication and transfer costs.
  #checkov:skip=CKV2_AWS_62:Only explicit batch-completed events trigger workflows, never per-image S3 notifications.
  bucket        = var.bucket_name
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "platform" {
  bucket                  = aws_s3_bucket.platform.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "platform" {
  bucket = aws_s3_bucket.platform.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "platform" {
  bucket = aws_s3_bucket.platform.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "platform" {
  bucket = aws_s3_bucket.platform.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  # Version/artifact deletion is deliberately manual to preserve lineage until review.
}

resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.platform.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.platform.arn, "${aws_s3_bucket.platform.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}

output "arn" { value = aws_s3_bucket.platform.arn }
output "name" { value = aws_s3_bucket.platform.id }
