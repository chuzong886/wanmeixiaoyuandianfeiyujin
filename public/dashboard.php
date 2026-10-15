<?php
header("Content-Security-Policy: default-src 'self'; script-src 'self' https://o.alicdn.com https://g.alicdn.com https://x.alicdn.com https://static-captcha.aliyuncs.com https://static-captcha-sgp.aliyuncs.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://*.alicdn.com https://*.aliyuncs.com; connect-src 'self' https://*.aliyuncs.com https://*.alicdn.com; frame-src https://*.aliyuncs.com https://*.alicdn.com; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
header('X-Content-Type-Options: nosniff');header('Referrer-Policy: no-referrer');header('Cache-Control: no-store');
readfile(__DIR__.'/dashboard.html');
