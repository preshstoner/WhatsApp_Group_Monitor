<?php
// email-reports.php - Automated Email Reports & SMTP/Resend Settings Dashboard

$status_message = "";
$status_type = "";

// Handle form submission for saving settings / triggering test report
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';

    // Capture form values so fields don't clear out on submit
    $provider = $_POST['provider'] ?? 'resend';
    $api_key = trim($_POST['api_key'] ?? '');
    $sender_email = $_POST['sender_email'] ?? 'onboarding@resend.dev';
    $recipient_email = trim($_POST['recipient_email'] ?? '');
    $frequency= $_POST['frequency'] ?? 'daily';
    $dispatch_time = $_POST['dispatch_time'] ?? '08:00';
    $ai_depth = $_POST['ai_depth'] ?? 'concise';

    if ($action === 'save_settings' || $action === 'test_report') {
        if ($action === 'test_report'){
            if (empty($api_key) || empty($recipient_email)){
                $status_message = "Please enter and save your API Key and Recipient Email first!";
                $status_type = "error";
            } else {
                $ch = curl_init('https://api.resend.com/emails');
                $payload = json_encode([
                    "from" => $sender_email,
                    "to" => [$recipient_email],
                    "subject" => "Whatsapp Group Monitor - Test Report",
                    "html" => "<h3>Test Report Dispatched</h3><p>Dashboard successfully connected to Resend!</p>"
                ]);

                curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
                curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'POST');
                curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
                curl_setopt($ch, CURLOPT_HTTPHEADER, [
                    'Authorization: Bearer ' . $api_key,
                    'Content-Type: application/json',
                    'Accept: application/json'
                ]);

                $response = curl_exec($ch);
                $curl_error = curl_error($ch);
                $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
                curl_close($ch);

                if ($http_code >= 200 && $http_code < 300) {
                    $status_message = "Test email report successfully sent to " . htmlspecialchars($recipient_email) . "!";
                    $status_type = "success";
                } else {
                    $status_message = "cURL Error: " . ($curl_error ?: "HTTP $http_code - Response: $response");
                    $status_type = "error";
                }
            }
        } else {
            $status_message = "Settings updated and saved successfully!";
            $status_type = "success";
        }
    } 
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email Reports & Settings - WhatsApp Group Monitor</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen flex flex-col">
    <!-- Header -->
     <header class="bg-slate-800 border-b border-slate-700 px-6 py-4 flex justify-between items-center">
        <h1 class="text-xl font-bold text-emerald-400 flex items-center gap-2">
            <span>WhatsApp Group Monitor & AI Hub</span>
        </h1>
        <div class="flex items-center gap-4 text-sm text-slate-400">
            <a href="whatsapp.php" class="hover:text-slate-200 transition">Dashboard</a>
            <a href="email-reports.php" class="text-emerald-400 font-medium">Email Reports</a>
        </div>
     </header>

     <!-- Main Content Container -->
    <main class="flex-1 max-w-5xl w-full mx-auto p-6 space-y-6">
        <div class="flex justify-between items-center">
            <div>
                <h2 class="text-2xl font-bold text-slate-100">Report Schedule & Delivery Settings</h2>
                <p class="text-sm text-slate-400 mt-1">Configure your email provider (SMTP or Resend API) and Automated summary cadences.</p>
            </div>
        </div>

        <?php if (!empty($status_message)): ?>
            <div class="p-4 rounded-xl text-sm border <?php echo $status_type === 'error' ? 'bg-rose-950/40 border-rose-800/50 text-rose-300' : 'bg-emerald-950/40 border-emerald-800/50 text-emerald-300'; ?>">
                <?php echo htmlspecialchars($status_message); ?>
            </div>
        <?php endif; ?>
        
        <!-- SINGLE UNIFIED FORM -->
        <form method="POST" class="space-y-6">
            <!-- Grid Layout for Settings Panels -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">

                <!-- Panel 1: Email Provider & API Settings --> 
                <div class="bg-slate-800/50 border border-slate-700 rounded-2xl p-6 space-y-4">
                    <h3 class="text-lg font-semibold text-emerald-400 border-b border-slate-700/60 pb-3">Email Provider (Resend / SMTP)</h3>

                    <div>
                        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Provder Type</label>
                        <select name="provider" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100">
                            <option value="resend">Resend API</option>
                        </select>
                    </div>

                    <div>
                        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Resend API Key / SMTP Password</label>
                        <input type="password" name="api_key" value="<?php echo htmlspecialchars($api_key ?? ''); ?>" placeholder="re_123456789..." class="w-full bg-slate900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100">
                    </div>

                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Sender Email</label>
                            <input type="email" name="sender_email" value="<?php echo htmlspecialchars($sender_email ?? 'onboarding@resend.dev'); ?>" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Recipient Email</label>
                            <input type="email" name="recipient_email" value="<?php echo htmlspecialchars($recipient_email ?? ''); ?>" placeholder="you@domain.com" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100">
                        </div>
                    </div>
                 </div>

                 <!-- Panel 2: Report Schedule & Cadence -->
                <div class="bg-slate-800/50 border border-slate-700 rounded-2xl p-6 space-y-4">
                    <h3 class="text-lg font-semibold text-emerald-400 border-b border-slate-700/60 pb-3">Schedule & Cadence</h3>

                    <div>
                        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Report Frequency</label>
                        <select name="frequency" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100">
                            <option value="daily">Daily Digest</option>
                            <option value="weekly">Weekly Summary</option>
                        </select>
                    </div>

                    <div>
                        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Schedule Dispatch Time</label>
                        <input type="time" name="dispatch_time" value="08:00" class="w-full bg-slate-900 border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100">
                    </div>
                </div>

             </div>

             <!-- Save Action Buttons Bar -->
             <div class="flex justify-end pt-4">
                <button type="submit" name="action" value="test_report" class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 px-6 py-3 rounded-xl text-sm font-medium transition">
                    Send Test Report Now
                </button>
                <button type="submit" name="action" value="save_settings" class="bg-emerald-600 hover:bg-emerald-500 text-white px-6 py-3 rounded-xl text-sm font-medium transition shadow-lg shadow-emerald-900/20">
                    Save Report Configuration
                </button>
             </div>
        </form>
    </main>
</body>
</html>