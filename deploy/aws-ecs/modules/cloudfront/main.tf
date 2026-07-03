locals {
  s3_origin_id           = "${var.name_prefix}-frontend-s3"
  alb_origin_id          = "${var.name_prefix}-api-alb"
  api_path_patterns_json = jsonencode(var.api_path_patterns)

  basic_auth_function_code = <<-EOT
    var crypto = require('crypto');

    function authorize(request) {
      var authHeader = request.headers.authorization ? request.headers.authorization.value : '';
      var suppliedHash = crypto.createHash('sha256').update(authHeader).digest('hex');

      if (suppliedHash === '${var.basic_auth_header_sha256}') {
        return null;
      }

      return {
        statusCode: 401,
        statusDescription: 'Unauthorized',
        headers: {
          'www-authenticate': { value: 'Basic realm="${var.basic_auth_realm}"' },
          'cache-control': { value: 'no-store' }
        }
      };
    }

    function handler(event) {
      var request = event.request;
      var authResponse = authorize(request);
      if (authResponse) {
        return authResponse;
      }
      return request;
    }
  EOT

  basic_auth_spa_rewrite_function_code = <<-EOT
    var crypto = require('crypto');
    var apiPathPatterns = ${local.api_path_patterns_json};

    function authorize(request) {
      var authHeader = request.headers.authorization ? request.headers.authorization.value : '';
      var suppliedHash = crypto.createHash('sha256').update(authHeader).digest('hex');

      if (suppliedHash === '${var.basic_auth_header_sha256}') {
        return null;
      }

      return {
        statusCode: 401,
        statusDescription: 'Unauthorized',
        headers: {
          'www-authenticate': { value: 'Basic realm="${var.basic_auth_realm}"' },
          'cache-control': { value: 'no-store' }
        }
      };
    }

    function hasFileExtension(uri) {
      var lastSegment = uri.substring(uri.lastIndexOf('/') + 1);
      return lastSegment.indexOf('.') !== -1;
    }

    function matchesApiPattern(uri, pattern) {
      if (pattern.charAt(pattern.length - 1) === '*') {
        return uri.indexOf(pattern.slice(0, -1)) === 0;
      }
      return uri === pattern;
    }

    function isApiPath(uri) {
      if (uri === '/api' || uri.indexOf('/api/') === 0) {
        return true;
      }
      for (var i = 0; i < apiPathPatterns.length; i++) {
        if (matchesApiPattern(uri, apiPathPatterns[i])) {
          return true;
        }
      }
      return false;
    }

    function handler(event) {
      var request = event.request;
      var authResponse = authorize(request);
      if (authResponse) {
        return authResponse;
      }

      if (!hasFileExtension(request.uri) && !isApiPath(request.uri)) {
        request.uri = '/index.html';
      }

      return request;
    }
  EOT
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.name_prefix}-frontend-oac"
  description                       = "OAC for private frontend assets"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "basic_auth" {
  name    = "${var.name_prefix}-basic-auth"
  runtime = "cloudfront-js-2.0"
  comment = "Basic auth gate for ${var.basic_auth_username}"
  publish = true

  code = local.basic_auth_function_code
}

resource "aws_cloudfront_function" "basic_auth_spa_rewrite" {
  name    = "${var.name_prefix}-basic-auth-spa-rewrite"
  runtime = "cloudfront-js-2.0"
  comment = "Basic auth gate and S3 SPA fallback for ${var.basic_auth_username}"
  publish = true

  code = local.basic_auth_spa_rewrite_function_code
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name_prefix} demo distribution"
  default_root_object = "index.html"
  price_class         = var.price_class
  wait_for_deployment = false

  origin {
    domain_name              = var.frontend_bucket_regional_domain_name
    origin_id                = local.s3_origin_id
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    domain_name = var.alb_dns_name
    origin_id   = local.alb_origin_id

    custom_header {
      name  = var.origin_verify_header_name
      value = var.origin_verify_header_value
    }

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = local.s3_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    default_ttl            = 60
    min_ttl                = 0
    max_ttl                = 86400

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.basic_auth_spa_rewrite.arn
    }
  }

  dynamic "ordered_cache_behavior" {
    for_each = var.api_path_patterns

    content {
      path_pattern           = ordered_cache_behavior.value
      target_origin_id       = local.alb_origin_id
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods         = ["GET", "HEAD"]
      compress               = true
      default_ttl            = 0
      min_ttl                = 0
      max_ttl                = 0

      forwarded_values {
        query_string = true
        headers      = ["*"]

        cookies {
          forward = "all"
        }
      }

      function_association {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.basic_auth.arn
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
