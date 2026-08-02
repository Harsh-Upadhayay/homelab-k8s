data "aws_caller_identity" "current" {}

data "aws_kms_key" "ssm" {
  key_id = "alias/aws/ssm"
}

resource "aws_iam_user" "eso" {
  name = "neovara-k8s-eso"
}

resource "aws_iam_access_key" "eso" {
  user = aws_iam_user.eso.name
}

# Lets the user do ONE thing: assume the role below. The user itself has no
# SSM/KMS permissions — the access key alone is useless without AssumeRole.
resource "aws_iam_user_policy" "eso_assume" {
  user = aws_iam_user.eso.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = aws_iam_role.eso_ssm.arn
    }]
  })
}

# The role holds the real permissions. Its trust policy says only the user
# above may assume it.
resource "aws_iam_role" "eso_ssm" {
  name = "neovara-k8s-eso-ssm"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = aws_iam_user.eso.arn }
    }]
  })
}

# What the role can actually do once assumed: read params under /neovara/* and
# decrypt the AWS-managed SSM key (required to read SecureStrings back).
resource "aws_iam_role_policy" "eso_ssm" {
  role = aws_iam_role.eso_ssm.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter*"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/neovara/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [data.aws_kms_key.ssm.arn]
      }
    ]
  })
}
