<?php
// Only for the local PHP development server. Production uses the Nginx rules in the deployment guide.
$path=parse_url($_SERVER['REQUEST_URI'],PHP_URL_PATH);
if($path==='/login'||$path==='/login/'){require __DIR__.'/login/index.php';return true;}
return false;
