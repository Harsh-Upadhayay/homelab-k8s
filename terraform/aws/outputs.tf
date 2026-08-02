output "eso_access_key_id" {
  value = aws_iam_access_key.eso.id
}

output "eso_access_key_secret" {
  value     = aws_iam_access_key.eso.secret
  sensitive = true
}