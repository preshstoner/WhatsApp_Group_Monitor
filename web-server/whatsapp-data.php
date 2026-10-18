<?php
// whatsapp-data.php - General AJAX Proxy
// header('Content-Type: application/json');

// 1. Endpoint Whitelist
$allowed_prefixes = ['/groups', '/messages', '/reports', '/settings', '/cron'];

$endpoint = $_GET['endpoint'] ?? '';
if (!$endpoint){
    http_response_code(400);
    echo json_encode(["error" => "Missing endpoint parameter"]);
    exit;
}



// Capture all query parameters sent from the frontend except 'endpoint'
$params = $_GET;
unset($params['endpoint']);
$queryString = http_build_query($params);


// 3. Forward Rwquest to FastAPI (Port 8086)
$fastapi_base = 'http://wa_fastapi:8000';
$target_url = $fastapi_base . $endpoint;
if ($queryString){
    $target_url .= '?' . $queryString;
}

$ch = curl_init($target_url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
// Forward the request method (GET/POST)
curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $_SERVER['REQUEST_METHOD']);

// If it's a POST request coming from frontend, forward POST data too
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
}

// Forward headers if needed
$response = curl_exec($ch);
$http_close = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curl_error = curl_error($ch);
curl_close($ch);

if ($response === false || !empty($curl_error)){
    header('Content-Type: application/json');
    http_response_code(500);
    echo json_encode(["error" => "Proxy connection failed: " . $curl_error]);
    exit;
}


http_response_code($http_close);
header('Content-Type: application/json');
echo $response; 