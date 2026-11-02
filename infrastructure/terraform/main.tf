data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

module "storage" {
  source      = "./modules/storage"
  bucket_name = var.bucket_name
}

resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_158:Demo uses AWS-managed encryption; a customer KMS key adds recurring cost.
  #checkov:skip=CKV_AWS_338:Seven-day demo retention is intentional; production retention needs an explicit policy.
  name              = "/aws/lambda/${var.project}-dataset-event"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "sagemaker" {
  #checkov:skip=CKV_AWS_158:Demo uses AWS-managed encryption; no sensitive imagery is logged.
  #checkov:skip=CKV_AWS_338:Short-lived demo has seven-day retention rather than a compliance archive.
  name              = "/aws/sagemaker/ProcessingJobs"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_event_bus" "platform" {
  name = var.project
}

resource "aws_sagemaker_model_package_group" "models" {
  model_package_group_name        = var.project
  model_package_group_description = "AeroIntel passing candidates; human approval remains mandatory"
}

resource "aws_iam_role" "sagemaker" {
  name = "${var.project}-sagemaker"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect    = "Allow", Principal = { Service = "sagemaker.amazonaws.com" }, Action = "sts:AssumeRole",
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "sagemaker" {
  role = aws_iam_role.sagemaker.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${module.storage.arn}/aerointel/*" },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = module.storage.arn,
      Condition = { StringLike = { "s3:prefix" = ["aerointel/*"] } } },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
      Resource = "${aws_cloudwatch_log_group.sagemaker.arn}:*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
      Resource = "arn:${data.aws_partition.current.partition}:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${var.project}*" },
      # This AWS API does not support resource-level permissions.
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["sagemaker:CreateProcessingJob", "sagemaker:DescribeProcessingJob", "sagemaker:StopProcessingJob"],
      Resource = "arn:${data.aws_partition.current.partition}:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:processing-job/${var.project}*" },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.sagemaker.arn,
      Condition = { StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" } } }
    ]
  })
}

resource "aws_sagemaker_pipeline" "workflow" {
  count                 = var.enable_training_pipeline ? 1 : 0
  pipeline_name         = var.project
  pipeline_display_name = "AeroIntel-local-contract-workflow"
  role_arn              = aws_iam_role.sagemaker.arn
  pipeline_definition = jsonencode({
    Version    = "2020-12-01"
    Parameters = [{ Name = "ManifestUri", Type = "String", DefaultValue = "s3://${var.bucket_name}/aerointel/input/manifest.json" }]
    Steps = [{
      Name = "LocalContractLifecycle", Type = "Processing", Arguments = {
        RoleArn     = aws_iam_role.sagemaker.arn,
        Environment = { AEROINTEL_BUCKET = var.bucket_name, DO_NOT_TRACK = "1", MLFLOW_DISABLE_TELEMETRY = "true" },
        AppSpecification = {
          ImageUri            = var.worker_image_uri,
          ContainerEntrypoint = ["python", "-m", "adapters.training.cloud_worker"],
          ContainerArguments  = ["--manifest-uri", { Get = "Parameters.ManifestUri" }]
        },
        ProcessingResources = { ClusterConfig = { InstanceCount = 1, InstanceType = "ml.m5.large", VolumeSizeInGB = 20 } },
        StoppingCondition   = { MaxRuntimeInSeconds = 3600 },
        NetworkConfig       = { EnableInterContainerTrafficEncryption = true, EnableNetworkIsolation = false },
        ProcessingOutputConfig = { Outputs = [{
          OutputName = "state", S3Output = {
            S3Uri     = { "Std:Join" = { On = "/", Values = ["s3://${var.bucket_name}/aerointel/executions", { Get = "Execution.PipelineExecutionId" }] } },
            LocalPath = "/opt/ml/processing/output", S3UploadMode = "EndOfJob"
          }
        }] }
      }
    }]
  })
  lifecycle {
    precondition {
      condition     = var.worker_image_uri != ""
      error_message = "Review/build a pinned worker image before enabling cloud execution."
    }
  }
}

resource "aws_iam_role" "event_handler" {
  name = "${var.project}-events"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}

resource "aws_iam_role_policy" "event_handler" {
  role = aws_iam_role.event_handler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.worker.arn}:*" },
    { Effect = "Allow", Action = ["sagemaker:StartPipelineExecution"],
    Resource = "arn:${data.aws_partition.current.partition}:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:pipeline/${var.project}" },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.failures.arn },
    { Effect = "Allow", Action = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"], Resource = "*" }
  ] })
}

data "archive_file" "handler" {
  type        = "zip"
  source_file = "${path.module}/lambda/handler.py"
  output_path = "${path.module}/.build/handler.zip"
}

resource "aws_lambda_function" "dataset_event" {
  #checkov:skip=CKV_AWS_272:Local source hash is pinned; AWS Signer rollout is a production hardening prerequisite.
  #checkov:skip=CKV_AWS_173:Environment contains only non-secret project configuration; service-managed encryption is used.
  #checkov:skip=CKV_AWS_117:Handler calls public AWS APIs only; a VPC/NAT adds cost without isolating an ingress surface.
  function_name                  = "${var.project}-dataset-event"
  role                           = aws_iam_role.event_handler.arn
  runtime                        = "python3.12"
  handler                        = "handler.handler"
  timeout                        = 30
  reserved_concurrent_executions = 1
  filename                       = data.archive_file.handler.output_path
  source_code_hash               = data.archive_file.handler.output_base64sha256
  dead_letter_config { target_arn = aws_sqs_queue.failures.arn }
  tracing_config { mode = "Active" }
  environment {
    variables = { PIPELINE_NAME = var.project, CLOUD_EXECUTION_ENABLED = tostring(var.enable_training_pipeline) }
  }
  depends_on = [aws_cloudwatch_log_group.worker]
}

resource "aws_cloudwatch_event_rule" "dataset" {
  name           = "${var.project}-dataset-completed"
  event_bus_name = aws_cloudwatch_event_bus.platform.name
  state          = var.enable_training_pipeline ? "ENABLED" : "DISABLED"
  event_pattern = jsonencode({ source = ["aerointel.platform"],
  "detail-type" = ["dataset-batch-completed"] })
}

resource "aws_cloudwatch_event_target" "dataset" {
  rule           = aws_cloudwatch_event_rule.dataset.name
  event_bus_name = aws_cloudwatch_event_bus.platform.name
  arn            = aws_lambda_function.dataset_event.arn
  dead_letter_config { arn = aws_sqs_queue.failures.arn }
  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 2
  }
}

resource "aws_sqs_queue" "failures" {
  name                      = "${var.project}-failures"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_policy" "failures" {
  queue_url = aws_sqs_queue.failures.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "events.amazonaws.com" },
    Action    = "sqs:SendMessage", Resource = aws_sqs_queue.failures.arn,
    Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.dataset.arn } }
  }] })
}

resource "aws_lambda_permission" "events" {
  statement_id  = "EventBridgeOnly"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dataset_event.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dataset.arn
}
