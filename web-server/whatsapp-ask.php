<?php
// whatsapp-ask.php - AI Analysis Proxy
header('Content-Type: application/json');

if ($_SERVER['REQUEST_METHOD'] !== 'POST'){
    http_response_code(405);
    echo json_encode(['error' => 'Method Not Allowed']);
    exit;
}

$input_data = file_get_contents('php://input');

if (empty($input_data)){
    http_response_code(400);
    echo json_encode(['error' => 'Empty request body']);
    exit;
}

//Must use host.docker.internal to reach FastAPI on the Windows host from inside Docker

// fn_target_url = 'http://host.docker.internal:8086/ask';
$target_url = 'http://host.docker.internal:8086/ask'; 

$ch = curl_init($target_url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'POST');
curl_setopt($ch, CURLOPT_POSTFIELDS, $input_data);
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    'Content-Type: application/json',
    'Accept: application/json'
]);
curl_setopt($ch, CURLOPT_TIMEOUT, 60); // Extended timeout for Claude responses

$response = curl_exec($ch);
$http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curl_error = curl_error($ch);
curl_close($ch);


if ($curl_error){
    http_response_code(502);
    echo json_encode(['error' => 'FastAPI AI Proxy Error: ' . $curl_error]);
    exit;
}

http_response_code($http_code);
echo $response;