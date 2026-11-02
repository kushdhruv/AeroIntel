output "bucket_name" { value = module.storage.name }
output "event_bus_name" { value = aws_cloudwatch_event_bus.platform.name }
output "sagemaker_execution_role_arn" { value = aws_iam_role.sagemaker.arn }
output "model_package_group_name" { value = aws_sagemaker_model_package_group.models.model_package_group_name }
