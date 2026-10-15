<?php
declare(strict_types=1);
$path=parse_url($_SERVER['REQUEST_URI'],PHP_URL_PATH);
if($path!=='/login'){if($_SERVER['REQUEST_METHOD']==='GET'&&$path==='/login/'){header('Location: /login',true,302);exit;}http_response_code(404);exit;}
if($_SERVER['REQUEST_METHOD']==='POST'){define('CAMPUS_AUTH_ROUTE',true);require dirname(__DIR__).'/api.php';}
else require dirname(__DIR__).'/dashboard.php';
